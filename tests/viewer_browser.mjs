// Optional browser regression: node tests/viewer_browser.mjs URL SCENARIO OUTDIR
// Requires Playwright and Chromium. SCENARIO: mixed, holdout, csg, saved, unsupported or inventory.
// mixed: box visible, cylinder hidden, unsupported entity; part comment below.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {chromium} = await import(process.env.ICADKIT_PLAYWRIGHT_MODULE || 'playwright');
const [url, scenario, output] = process.argv.slice(2);
const report = {scenario, errors: [], external: [], badResponses: [], checks: []};
await mkdir(output, {recursive: true});
const browser = await chromium.launch({headless: true, args: ['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader-webgl']});
try {
  const page = await browser.newPage({viewport:{width:1440,height:950}});
  page.on('pageerror', e => report.errors.push(String(e)));
  page.on('console', m => {if(m.type()==='error') report.errors.push(m.text());});
  page.on('response', r => {if(!r.ok()) report.badResponses.push([r.status(),r.url()]);});
  await page.route('**/*', async route => {
    if(new URL(route.request().url()).origin !== new URL(url).origin) {report.external.push(route.request().url());await route.abort();}
    else await route.continue();
  });
  await page.goto(url);
  await page.waitForFunction(()=>document.getElementById('app').dataset.status !== 'loading');
  assert.equal(await page.locator('#app').getAttribute('data-status'),'ready');
  await page.evaluate(async()=>{window.nativeView=await(await import('./viewer.js')).application;});
  if (scenario === 'inventory') {
    assert.match(await page.locator('#scope').innerText(),/Geometry, placement and appearance are unavailable/);
    assert.match(await page.locator('#details').innerText(),/Unqualified/);
    assert.equal(await page.evaluate(()=>nativeView.scene.length_unit),null);
    assert.equal(await page.evaluate(()=>nativeView.scene.coordinate_system),null);
    assert.match(await page.locator('#view-status').innerText(),/Part inventory/);
  } else {
    assert.match(await page.locator('#scope').innerText(),/full-model coverage is unverified/);
  }
  report.checks.push('WebGL ready; profile scope visible; no external requests');
  report.renderer=await page.evaluate(()=>{const gl=nativeView.gl,info=gl.getExtension('WEBGL_debug_renderer_info');return info?gl.getParameter(info.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);});
  if (scenario === 'mixed') {
    assert.equal(await page.evaluate(()=>nativeView.visible.size),1);
    assert.equal(await page.locator('.tree-row.unsupported').count(),1);
    await page.locator('#show-all').click();
    assert.equal(await page.evaluate(()=>nativeView.visible.size),2);
    await page.locator('#restore-visibility').click();
    assert.equal(await page.evaluate(()=>nativeView.visible.size),1);
    await page.getByRole('button',{name:'部品',exact:true}).click();
    assert.match(await page.locator('#details').innerText(),/<img src=x onerror=alert\(1\)>/);
    assert.equal(await page.locator('#details img').count(),0);
    await page.locator('#search').fill('Unsupported');
    assert.equal(await page.locator('.tree-row:visible').count(),1);
    await page.locator('.tree-row.unsupported button').click();
    assert.match(await page.locator('#details').innerText(),/not drawn/);
    await page.locator('#search').fill('');
    const rootCheck=page.locator('.tree-row input').first();
    await rootCheck.check();
    await rootCheck.uncheck();
    assert.equal(await page.evaluate(()=>nativeView.visible.size),0);
    assert.equal(await page.locator('#empty').isVisible(),true);
    await rootCheck.check();
    assert.equal(await page.evaluate(()=>nativeView.visible.size),2);
    report.checks.push('saved hidden/restored; parent visibility; unsupported selectable; search; stored text rendered literally');
  }
  if (scenario === 'saved') {
    assert.equal(await page.evaluate(()=>nativeView.scene.saved_brep.evaluated),1);
    assert.equal(await page.evaluate(()=>nativeView.drawables.length),1);
    await page.getByRole('button',{name:/^Saved component/}).first().click();
    assert.match(await page.locator('#details').innerText(),/not drawn separately/);
    await page.getByRole('button',{name:/^Saved final body/}).click();
    assert.match(await page.locator('#details').innerText(),/Volume \(mm³\)/);
    report.checks.push('saved final body selectable; components not drawn separately; mass properties visible');
  }
  if (scenario === 'csg') {
    assert.equal(await page.evaluate(()=>nativeView.scene.csg.evaluated),1);
    assert.equal(await page.evaluate(()=>nativeView.drawables.length),1);
    await page.getByRole('button',{name:/^CSG operand/}).first().click();
    assert.match(await page.locator('#details').innerText(),/not drawn separately/);
    await page.getByRole('button',{name:/^CSG result/}).click();
    assert.match(await page.locator('#details').innerText(),/Volume \(mm³\)/);
    // SDK subtract fixture: the through-hole is centered at (10, 12) mm.
    await page.locator('#camera').selectOption('top');
    await page.locator('#fit').click();
    const hole=await page.evaluate(()=>{
      const v=nativeView,s=v.scene,p=[10,12,20].map((a,i)=>(a-s.render_origin_mm[i])/s.render_scale_mm),m=v.matrix();
      const x=m[0]*p[0]+m[4]*p[1]+m[8]*p[2]+m[12],y=m[1]*p[0]+m[5]*p[1]+m[9]*p[2]+m[13];
      const r=v.canvas.getBoundingClientRect();return {x:r.x+(x+1)*r.width/2,y:r.y+(1-y)*r.height/2};
    });
    await page.mouse.click(hole.x,hole.y);
    assert.equal(await page.evaluate(()=>nativeView.selectionId),null);
    report.checks.push('one final CSG mesh; operands retained without drawing; mass properties; picking through hole reaches background');
  }
  if(!['unsupported','inventory'].includes(scenario)) {
    for(const view of ['top','front','right','iso']) await page.locator('#camera').selectOption(view);
    await page.locator('#fit').click();
    await page.locator('#edges').uncheck(); await page.locator('#edges').check();
    // Project the centroid of a real triangle, then select via an actual pointer.
    const point=await page.evaluate(()=>{
      const v=nativeView, item=v.drawables.find(d=>v.visible.has(d.id)),mesh=v.scene.meshes[item.id];
      const ids=mesh.triangles.slice(0,3),p=[0,1,2].map(k=>ids.reduce((s,i)=>s+mesh.positions[3*i+k],0)/3),m=v.matrix();
      const x=m[0]*p[0]+m[4]*p[1]+m[8]*p[2]+m[12],y=m[1]*p[0]+m[5]*p[1]+m[9]*p[2]+m[13];
      const r=v.canvas.getBoundingClientRect();return {x:r.x+(x+1)*r.width/2,y:r.y+(1-y)*r.height/2,id:item.id};
    });
    await page.mouse.click(point.x,point.y);
    assert.equal(await page.evaluate(()=>nativeView.selectionId),point.id);
    assert.match(await page.locator('#details').innerText(),['csg','saved'].includes(scenario) ? /Volume \(mm³\)/ : /Height \(mm\)/);
    const before=await page.evaluate(()=>({yaw:nativeView.yaw,target:[...nativeView.target],radius:nativeView.radius}));
    await page.mouse.move(point.x,point.y);await page.mouse.down();await page.mouse.move(point.x+45,point.y+25,{steps:4});await page.mouse.up();
    assert.notEqual(await page.evaluate(()=>nativeView.yaw),before.yaw);
    await page.mouse.down({button:'right'});await page.mouse.move(point.x+70,point.y+40,{steps:3});await page.mouse.up({button:'right'});
    assert.notDeepEqual(await page.evaluate(()=>nativeView.target),before.target);
    await page.mouse.wheel(0,150);await page.waitForTimeout(100);
    assert.notEqual(await page.evaluate(()=>nativeView.radius),before.radius);
    await page.locator('#fit').click();
    report.checks.push('camera presets; edges; real pointer picking; orbit; pan; zoom; fit');
  } else {
    assert.equal(await page.evaluate(()=>nativeView.drawables.length),0);
    assert.equal(await page.locator('#empty').isVisible(),true);
    await page.locator('.tree-row.unsupported button').first().click();
    assert.match(await page.locator('#details').innerText(),/not drawn/);
    report.checks.push('empty geometry retains selectable inventory and diagnostic');
  }
  if (scenario === 'inventory') {
    const name=await page.evaluate(()=>nativeView.scene.parts.find(p=>!p.is_root).name);
    await page.locator('#search').fill(name);
    await page.getByRole('button',{name,exact:true}).first().click();
    assert.match(await page.locator('#details').innerText(),/Stored attributes/);
    assert.match(await page.locator('#details').innerText(),/evaluated placement is unavailable/);
    assert.doesNotMatch(await page.locator('#details').innerText(),/\(mm\)/);
    await page.locator('#search').fill('');
    report.checks.push('inventory search and stored attributes; no invented units or frames');
  }
  await page.screenshot({path:join(output,`viewer-${scenario}.png`)});
  await page.setViewportSize({width:600,height:850});
  await page.waitForTimeout(100);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  report.checks.push('narrow viewport without horizontal overflow');
  if (scenario === 'mixed') {
    const fallback = await browser.newPage();
    await fallback.addInitScript(() => {
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function(kind, ...args) {
        return kind === 'webgl' ? null : getContext.call(this, kind, ...args);
      };
    });
    await fallback.goto(url);
    await fallback.waitForFunction(()=>document.getElementById('app').dataset.status==='no-webgl');
    await fallback.getByRole('button',{name:'部品',exact:true}).click();
    assert.match(await fallback.locator('#details').innerText(),/Stored attributes/);
    assert.match(await fallback.locator('#empty').innerText(),/WebGL is unavailable/);
    report.checks.push('WebGL unavailable retains readable part properties');
  }
  assert.deepEqual(report.errors,[]);assert.deepEqual(report.external,[]);assert.deepEqual(report.badResponses,[]);
  report.passed=true;
} catch(error) {report.passed=false;report.error=String(error.stack);process.exitCode=1;}
finally {await browser.close();await writeFile(join(output,`browser-${scenario}.json`),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));}
