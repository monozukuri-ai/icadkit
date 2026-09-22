// Native viewer. Original icadkit code; distributed under the project license.
// No external assets, CAD kernel, or inferred placement/color mapping.
const $ = id => document.getElementById(id);
const dot = (a, b) => a.reduce((v, x, i) => v + x * b[i], 0);
const sub = (a, b) => a.map((v, i) => v - b[i]);
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const norm = a => { const n = Math.hypot(...a); return a.map(v => v / (n || 1)); };
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = String(text);
  if (className) el.className = className;
  return el;
};
const valueText = value => value === null || value === undefined ? 'Unknown / not decoded' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);

class NativeView {
  constructor(scene) {
    this.scene = scene;
    this.parts = new Map(scene.parts.map(p => [p.part_id, p]));
    this.entities = new Map(scene.parts.flatMap(p => p.entities.map(e => [e.entity_id, e])));
    this.visible = new Set([...this.entities.values()].filter(e => e.appearance.visible !== false && scene.meshes[e.entity_id]).map(e => e.entity_id));
    this.selected = new Set();
    this.rows = new Map();
    this.children = new Map();
    for (const p of this.parts.values()) {
      if (!this.children.has(p.parent_id)) this.children.set(p.parent_id, []);
      this.children.get(p.parent_id).push(p);
    }
    this.canvas = $('canvas');
    this.gl = this.canvas.getContext('webgl', {antialias: true, preserveDrawingBuffer: true});
    this.yaw = -Math.PI / 3;
    this.pitch = Math.PI / 5;
    this.target = [0, 0, 0];
    this.radius = 2;
    this.showEdges = true;
    this.drawables = [];
    this.buildTree();
    this.describeModel();
    this.bindControls();
    if (!this.gl) {
      $('empty').hidden = false;
      $('empty').textContent = 'WebGL is unavailable. Parts and properties are still available.';
      $('app').dataset.status = 'no-webgl';
      return;
    }
    this.prepareGL();
    this.fit();
    new ResizeObserver(() => this.draw()).observe(this.canvas);
    this.canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      $('app').dataset.status = 'context-lost';
      $('empty').hidden = false;
      $('empty').textContent = 'Graphics context lost. Reload to restore the view.';
    });
    $('app').dataset.status = 'ready';
  }

  prepareGL() {
    const gl = this.gl;
    const shader = (type, source) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, source); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw Error(gl.getShaderInfoLog(s));
      return s;
    };
    this.program = gl.createProgram();
    gl.attachShader(this.program, shader(gl.VERTEX_SHADER, `
      attribute vec3 position; attribute vec3 normal;
      uniform mat4 matrix; varying vec3 n;
      void main() { n = normal; gl_Position = matrix * vec4(position, 1.0); }
    `));
    gl.attachShader(this.program, shader(gl.FRAGMENT_SHADER, `
      precision mediump float;
      uniform vec3 color; uniform bool unlit; varying vec3 n;
      void main() {
        float light = unlit ? 1.0 : 0.42 + 0.58 * max(dot(normalize(n), normalize(vec3(0.4,-0.6,1.0))), 0.0);
        gl_FragColor = vec4(color * light, 1.0);
      }
    `));
    gl.linkProgram(this.program);
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(this.program));
    gl.useProgram(this.program);
    this.position = gl.getAttribLocation(this.program, 'position');
    this.normal = gl.getAttribLocation(this.program, 'normal');
    this.matrixLocation = gl.getUniformLocation(this.program, 'matrix');
    this.color = gl.getUniformLocation(this.program, 'color');
    this.unlit = gl.getUniformLocation(this.program, 'unlit');
    const buffer = values => {
      const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(values), gl.STATIC_DRAW); return b;
    };
    for (const [id, mesh] of Object.entries(this.scene.meshes)) {
      const points = index => mesh.positions.slice(3 * index, 3 * index + 3);
      const faces = [];
      for (let i = 0; i < mesh.triangles.length; i += 3) {
        const [a, b, c] = mesh.triangles.slice(i, i + 3).map(points);
        const n = norm(cross(sub(b, a), sub(c, a)));
        faces.push(...a, ...n, ...b, ...n, ...c, ...n);
      }
      const edges = mesh.edges.flatMap(i => [...points(i), 0, 0, 1]);
      this.drawables.push({id, faces: buffer(faces), edges: buffer(edges),
        count: mesh.triangles.length, edgeCount: mesh.edges.length, pick: this.drawables.length + 1});
    }
    gl.enable(gl.DEPTH_TEST);
    gl.disable(gl.DITHER);
  }

  basis() {
    const c = Math.cos(this.yaw), s = Math.sin(this.yaw), cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    return {right: [-s, c, 0], up: [-sp*c, -sp*s, cp], direction: [cp*c, cp*s, sp]};
  }

  matrix() {
    const {right, up, direction} = this.basis();
    const aspect = this.canvas.width / this.canvas.height;
    const depth = 10 + Math.hypot(...this.target);
    const x = right.map(v => v / (this.radius * aspect));
    const y = up.map(v => v / this.radius);
    const z = direction.map(v => -v / depth);
    return new Float32Array([x[0],y[0],z[0],0, x[1],y[1],z[1],0, x[2],y[2],z[2],0,
      -dot(x,this.target),-dot(y,this.target),-dot(z,this.target),1]);
  }

  draw(picking = false) {
    if (!this.gl || !this.program || this.gl.isContextLost()) return;
    const gl = this.gl;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = this.canvas.getBoundingClientRect();
    const w = Math.max(1, Math.round(rect.width*dpr)), h = Math.max(1, Math.round(rect.height*dpr));
    if (this.canvas.width !== w || this.canvas.height !== h) { this.canvas.width = w; this.canvas.height = h; }
    gl.viewport(0, 0, w, h);
    gl.clearColor(...(picking ? [0,0,0,1] : [0.925,0.945,0.965,1]));
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.uniformMatrix4fv(this.matrixLocation, false, this.matrix());
    gl.uniform1i(this.unlit, picking);
    const bind = buffer => {
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.enableVertexAttribArray(this.position); gl.vertexAttribPointer(this.position, 3, gl.FLOAT, false, 24, 0);
      gl.enableVertexAttribArray(this.normal); gl.vertexAttribPointer(this.normal, 3, gl.FLOAT, false, 24, 12);
    };
    gl.enable(gl.POLYGON_OFFSET_FILL); gl.polygonOffset(1, 1);
    for (const item of this.drawables) {
      if (!this.visible.has(item.id)) continue;
      const rgb = picking ? [(item.pick&255)/255, ((item.pick>>8)&255)/255, ((item.pick>>16)&255)/255]
        : this.selected.has(item.id) ? [0.98,0.67,0.22] : [0.30,0.65,0.72];
      gl.uniform3fv(this.color, rgb); bind(item.faces); gl.drawArrays(gl.TRIANGLES, 0, item.count);
    }
    gl.disable(gl.POLYGON_OFFSET_FILL);
    if (!picking && this.showEdges) {
      gl.uniform1i(this.unlit, true); gl.uniform3fv(this.color, [0.13,0.27,0.35]);
      for (const item of this.drawables) {
        if (!this.visible.has(item.id)) continue;
        bind(item.edges); gl.drawArrays(gl.LINES, 0, item.edgeCount);
      }
    }
    if (!picking) {
      const count = this.visible.size;
      $('view-status').textContent = `${count} visible · mm · Z up`;
      $('empty').hidden = count > 0;
      $('empty').textContent = this.drawables.length ? 'All supported shapes are hidden. Use Show all to inspect them.' : 'No supported native shapes. Select a part or entity to inspect its properties.';
    }
  }

  fit() {
    const lo = [Infinity,Infinity,Infinity], hi = [-Infinity,-Infinity,-Infinity];
    const {right,up,direction} = this.basis();
    const axes = [right,up,direction];
    for (const [id, mesh] of Object.entries(this.scene.meshes)) {
      if (!this.visible.has(id)) continue;
      for (let i=0;i<mesh.positions.length;i+=3) {
        const point = mesh.positions.slice(i,i+3);
        axes.forEach((axis,k) => { const v=dot(axis,point); lo[k]=Math.min(lo[k],v); hi[k]=Math.max(hi[k],v); });
      }
    }
    if (Number.isFinite(lo[0])) {
      const center = lo.map((v,i) => (v+hi[i])/2);
      this.target = [0,1,2].map(i => axes.reduce((sum,axis,k) => sum+axis[i]*center[k],0));
      const rect = this.canvas.getBoundingClientRect();
      this.radius = Math.max(1e-6, hi[1]-lo[1], (hi[0]-lo[0])/Math.max(0.01,rect.width/Math.max(1,rect.height))) * 0.59;
    }
    this.draw();
  }

  pick(clientX, clientY) {
    if (!this.gl) return;
    this.draw(true);
    const rect = this.canvas.getBoundingClientRect(), pixel = new Uint8Array(4);
    this.gl.readPixels(Math.floor((clientX-rect.left)*this.canvas.width/rect.width),
      this.canvas.height-1-Math.floor((clientY-rect.top)*this.canvas.height/rect.height), 1,1,this.gl.RGBA,this.gl.UNSIGNED_BYTE,pixel);
    const id = pixel[0] + (pixel[1]<<8) + (pixel[2]<<16);
    if (id > 0 && id <= this.drawables.length) this.select(this.drawables[id-1].id);
    else { this.selected.clear(); this.describeModel(); this.updateRows(); }
    this.draw();
  }

  entityIds(partId) {
    const ids = [], seen = new Set(), stack = [partId];
    while (stack.length) {
      const id = stack.pop(); if (seen.has(id)) continue; seen.add(id);
      const part = this.parts.get(id); if (!part) continue;
      ids.push(...part.entities.map(e => e.entity_id));
      stack.push(...(this.children.get(id) || []).map(p => p.part_id));
    }
    return ids;
  }

  buildTree() {
    $('part-count').textContent = this.parts.size;
    const summary = this.scene.summary;
    $('scope').textContent = `Native boxes & cylinders · ${summary.rendered} / ${summary.entities} indexed entities represented · ${summary.omitted} unsupported or invalid · ${this.scene.resource_count} embedded resources not displayed. Colors are illustrative; full-model coverage is unverified.`;
    const seen = new Set();
    const addRow = (parent, id, label, tag, unsupported) => {
      const row = node('div', undefined, `tree-row${unsupported ? ' unsupported' : ''}`);
      const checkbox = node('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', `Show ${label}`);
      const ids = this.parts.has(id) ? this.entityIds(id) : [id];
      checkbox.disabled = !ids.some(e => this.scene.meshes[e]);
      checkbox.addEventListener('change', () => {
        for (const e of ids) if (this.scene.meshes[e]) checkbox.checked ? this.visible.add(e) : this.visible.delete(e);
        this.updateRows(); this.draw();
      });
      const button = node('button', label); button.title = `${label} (${id})`;
      button.addEventListener('click', () => this.select(id));
      row.append(checkbox, button, node('span', tag, 'shape-tag')); parent.append(row);
      this.rows.set(id, {row, checkbox, ids, search: `${label} ${id} ${tag}`.toLowerCase()});
    };
    // Iterative traversal keeps malformed/disconnected hierarchies visible once.
    const roots = [...this.parts.values()].filter(p => !this.parts.has(p.parent_id));
    for (const start of [...roots, ...this.parts.values()]) {
      const stack = [[start, $('tree')]];
      while (stack.length) {
        const [part, parent] = stack.pop();
        if (seen.has(part.part_id)) continue; seen.add(part.part_id);
        addRow(parent, part.part_id, part.name ?? '(undecoded name)', part.is_external ? 'external' : part.is_root ? 'root' : 'part', false);
        const branch = node('div', undefined, 'branch'); parent.append(branch);
        for (const entity of part.entities) addRow(branch, entity.entity_id, `${entity.primitive?.kind || 'Unsupported'} · ${entity.entity_id}`, entity.geometry_status === 'complete' ? '' : entity.geometry_status, !entity.primitive);
        const children = this.children.get(part.part_id) || [];
        for (let i=children.length-1;i>=0;i--) stack.push([children[i], branch]);
      }
    }
    this.updateRows();
  }

  updateRows() {
    for (const [id, {row,checkbox,ids}] of this.rows) {
      const eligible = ids.filter(e => this.scene.meshes[e]);
      const shown = eligible.filter(e => this.visible.has(e)).length;
      checkbox.checked = eligible.length > 0 && shown === eligible.length;
      checkbox.indeterminate = shown > 0 && shown < eligible.length;
      row.classList.toggle('dimmed', eligible.length > 0 && shown === 0);
      row.classList.toggle('selected', id === this.selectionId || this.selected.has(id));
    }
  }

  fields(entries, parent = $('details')) {
    const dl = node('dl');
    for (const [key,value] of entries) dl.append(node('dt',key), node('dd',valueText(value)));
    parent.append(dl);
  }

  describeModel() {
    this.selectionId = null;
    $('details').replaceChildren(node('h3','Native model'), node('p','Select a part in the tree or click a shape to inspect its saved properties.'));
    $('details').append(node('p','This view renders qualified native boxes and cylinders. Unsupported shapes and unloaded external references stay in the tree.', 'notice'));
    this.fields([['Units','mm · 3DGLOBAL'],['Represented entities',this.scene.summary.rendered],['Unsupported / invalid entities',this.scene.summary.omitted],['Appearance','Illustrative colors; saved entity visibility']]);
    const details = node('details'); details.append(node('summary','Read diagnostics & source'));
    this.fields([['Source SHA-256',this.scene.source_sha256],['Read status',this.scene.part_status],['Diagnostics',this.scene.diagnostics],['Unparsed native ranges',this.scene.opaque_ranges]], details);
    $('details').append(details);
  }

  select(id) {
    this.selectionId = id;
    const part = this.parts.get(id), entity = this.entities.get(id);
    this.selected = new Set(part ? this.entityIds(id) : [id]);
    $('details').replaceChildren(node('h3', part ? part.name ?? '(undecoded name)' : entity.primitive?.kind || 'Unsupported entity'));
    if (part) {
      this.fields([['Part ID',part.part_id],['Parent ID',part.parent_id],['Source ID',part.source_id],['External reference',part.external_reference],['Placement status',part.placement_status],['Part world frame (mm)',part.world_transform],['Native geometry status',part.native_geometry_status]]);
      $('details').append(node('h3','Stored attributes'));
      this.fields(part.properties.map(p => [p.name,p.value]));
      $('details').append(node('p','Part frames are shown for inspection. Native shapes already use their saved global frame.'));
    } else {
      this.fields([['Entity ID',id],['Owner',this.parts.get(entity.owner_id)?.name ?? entity.owner_id],['Geometry status',entity.geometry_status],['Source byte range',entity.byte_range],['Saved visibility',entity.appearance.visible],['Palette index (not RGB)',entity.appearance.color_index],['Layer',entity.appearance.layer],['Appearance status',entity.appearance.status]]);
      if (entity.primitive) this.fields([['Dimensions (mm)',entity.primitive.box_dimensions],['Radius (mm)',entity.primitive.radius],['Height (mm)',entity.primitive.height],['Global frame (mm)',entity.primitive.world_transform]]);
      else $('details').append(node('p','This entity is not drawn. Its geometry is unsupported or invalid.', 'notice'));
      this.fields([['Diagnostics',entity.diagnostics]]);
    }
    this.updateRows(); this.draw();
  }

  bindControls() {
    $('fit').addEventListener('click', () => this.fit());
    $('edges').addEventListener('change', e => { this.showEdges = e.target.checked; this.draw(); });
    $('camera').addEventListener('change', e => {
      [this.yaw,this.pitch] = {iso:[-Math.PI/3,Math.PI/5],top:[-Math.PI/2,Math.PI/2],front:[-Math.PI/2,0],right:[0,0]}[e.target.value]; this.draw();
    });
    $('show-all').addEventListener('click', () => { this.visible = new Set(Object.keys(this.scene.meshes)); this.updateRows(); this.fit(); });
    $('restore-visibility').addEventListener('click', () => {
      this.visible = new Set([...this.entities.values()].filter(e => e.appearance.visible !== false && this.scene.meshes[e.entity_id]).map(e => e.entity_id)); this.updateRows(); this.fit();
    });
    $('search').addEventListener('input', e => {
      const term = e.target.value.toLowerCase();
      for (const item of this.rows.values()) item.row.hidden = !item.search.includes(term);
    });
    let drag = null;
    this.canvas.addEventListener('contextmenu', e => e.preventDefault());
    this.canvas.addEventListener('pointerdown', e => {
      if (e.button > 2) return;
      this.canvas.focus(); this.canvas.setPointerCapture(e.pointerId);
      drag = {x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,pan:e.button!==0||e.shiftKey,moved:false};
    });
    this.canvas.addEventListener('pointermove', e => {
      if (!drag) return;
      const dx=e.clientX-drag.x, dy=e.clientY-drag.y;
      drag.moved ||= Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)>3;
      if (drag.pan) {
        const {right,up}=this.basis(), factor=2*this.radius/this.canvas.clientHeight;
        this.target=this.target.map((v,i)=>v-dx*factor*right[i]+dy*factor*up[i]);
      } else { this.yaw-=dx*0.007; this.pitch=Math.max(-Math.PI/2+0.001,Math.min(Math.PI/2-0.001,this.pitch+dy*0.007)); }
      drag.x=e.clientX; drag.y=e.clientY; this.draw();
    });
    this.canvas.addEventListener('pointerup', e => { if (drag && !drag.moved && !drag.pan) this.pick(e.clientX,e.clientY); drag=null; });
    this.canvas.addEventListener('pointercancel', () => {drag=null;});
    this.canvas.addEventListener('wheel', e => { e.preventDefault(); this.radius=Math.max(1e-6,Math.min(1e4,this.radius*Math.exp(Math.max(-1,Math.min(1,e.deltaY*0.001))))); this.draw(); }, {passive:false});
    this.canvas.addEventListener('keydown', e => { if (e.key.toLowerCase()==='f') {e.preventDefault();this.fit();} });
  }
}

export const application = (async () => {
  try {
    const response = await fetch('scene.json');
    if (!response.ok) throw Error(`Cannot load scene: HTTP ${response.status}`);
    const scene = await response.json();
    if (scene.schema_version !== 1 || scene.scope !== 'qualified_native_primitives') throw Error('Unsupported native scene schema');
    return new NativeView(scene);
  } catch (error) {
    $('app').dataset.status = 'error';
    $('scope').textContent = `Viewer could not open this scene: ${error.message}`;
    $('empty').hidden = false; $('empty').textContent = 'No geometry was displayed.';
    throw error;
  }
})();
