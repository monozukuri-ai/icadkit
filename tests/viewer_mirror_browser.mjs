// Optional real-file mirror check: node tests/viewer_mirror_browser.mjs URL MODE OUTDIR
// MODE: native, saved or ambiguous. Inputs and outputs stay caller-owned.
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const {chromium} = await import(process.env.ICADKIT_PLAYWRIGHT_MODULE || 'playwright');
const [url, mode, output] = process.argv.slice(2);
await mkdir(output, {recursive:true});
const report = {mode, errors:[], external:[], picked:[], checks:[]};
const browser = await chromium.launch({headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader','--use-angle=swiftshader-webgl']});
try {
  const page = await browser.newPage({viewport:{width:1440,height:950}});
  page.on('pageerror', e => report.errors.push(String(e)));
  page.on('console', m => {if(m.type()==='error') report.errors.push(m.text());});
  await page.route('**/*', async route => {
    if(new URL(route.request().url()).origin !== new URL(url).origin) {
      report.external.push(route.request().url()); await route.abort();
    } else await route.continue();
  });
  await page.goto(url);
  await page.waitForFunction(()=>document.getElementById('app').dataset.status!=='loading');
  assert.equal(await page.locator('#app').getAttribute('data-status'),'ready');
  await page.evaluate(async()=>{window.v=await(await import('./viewer.js')).application;});
  assert.match(await page.locator('#scope').innerText(),/full-model coverage is unverified/);
  const items = await page.evaluate(()=>v.drawables.map(d=>({id:d.id,owner:v.entities.get(d.id).owner_id})));
  report.drawables=items.length;
  if(mode==='ambiguous') {
    assert.equal(items.length,0);
    assert.equal(await page.locator('#empty').isVisible(),true);
    const id=await page.evaluate(()=>[...v.entities.values()].find(e=>e.saved_body).entity_id);
    await page.locator(`button[title$="(${id})"]`).click();
    assert.match(await page.locator('#details').innerText(),/saved.owner_resource_keys/);
    report.checks.push('ambiguous saved resource association retained with diagnostic and no mesh');
  } else {
    assert.ok(items.length>1);
    if(mode==='saved') assert.equal(await page.evaluate(()=>v.scene.saved_brep.evaluated),items.length);
    assert.ok(await page.evaluate(()=>[...v.parts.values()].some(p=>p.is_mirror)));
    for(const item of items) {
      // Isolate each occurrence so coincident double mirrors cannot hide a pick target.
      const rootId=await page.evaluate(()=>v.scene.parts.find(p=>p.is_root).part_id);
      await page.locator(`.tree-row:has(button[title$="(${rootId})"]) input`).check();
      await page.locator(`.tree-row:has(button[title$="(${rootId})"]) input`).uncheck();
      await page.locator(`.tree-row:has(button[title$="(${item.id})"]) input`).check();
      await page.locator('#camera').selectOption('iso');
      await page.locator('#fit').click();
      const pointer=await page.evaluate(id=>{
        const mesh=v.scene.meshes[id],ids=mesh.triangles.slice(0,3);
        const p=[0,1,2].map(k=>ids.reduce((sum,i)=>sum+mesh.positions[3*i+k],0)/3),m=v.matrix();
        const x=m[0]*p[0]+m[4]*p[1]+m[8]*p[2]+m[12],y=m[1]*p[0]+m[5]*p[1]+m[9]*p[2]+m[13];
        const r=v.canvas.getBoundingClientRect();return {x:r.x+(x+1)*r.width/2,y:r.y+(1-y)*r.height/2};
      },item.id);
      await page.mouse.click(pointer.x,pointer.y);
      assert.equal(await page.evaluate(()=>v.selectionId),item.id);
      assert.equal(await page.evaluate(()=>v.entities.get(v.selectionId).owner_id),item.owner);
      const owner=await page.evaluate(id=>v.parts.get(id),item.owner);
      assert.equal(await page.locator('#details dt').filter({hasText:/^Owner$/}).locator('xpath=following-sibling::dd[1]').innerText(),owner.name);
      await page.locator(`button[title$="(${item.owner})"]`).click();
      assert.match(await page.locator('#details').innerText(),/Mirrored occurrence/);
      assert.match(await page.locator('#details').innerText(),/Occurrence orientation/);
      assert.ok(await page.evaluate(id=>v.selected.has(id),item.id));
      report.picked.push({...item,mirrored_owner:owner.is_mirror});
    }
    await page.locator('#show-all').click();
    assert.equal(await page.evaluate(()=>v.visible.size),items.length);
    const parentId=await page.evaluate(()=>v.scene.parts.find(p=>p.is_mirror&&v.entityIds(p.part_id).some(id=>v.scene.meshes[id])).part_id);
    const descendants=await page.evaluate(id=>v.entityIds(id).filter(e=>v.scene.meshes[e]),parentId);
    await page.locator(`.tree-row:has(button[title$="(${parentId})"]) input`).uncheck();
    assert.equal(await page.evaluate(()=>v.visible.size),items.length-descendants.length);
    await page.locator(`.tree-row:has(button[title$="(${parentId})"]) input`).check();
    assert.equal(await page.evaluate(()=>v.visible.size),items.length);
    await page.locator('#restore-visibility').click();
    assert.equal(await page.evaluate(()=>v.visible.size),await page.evaluate(()=>[...v.entities.values()].filter(e=>v.scene.meshes[e.entity_id]&&e.appearance.visible!==false).length));
    await page.locator('#fit').click();
    report.checks.push('real pointer picks every occurrence; owner identity; part selection; mirror parent visibility; saved visibility restore');
  }
  await page.screenshot({path:join(output,'viewer.png')});
  report.renderer=await page.evaluate(()=>{const gl=v.gl,e=gl.getExtension('WEBGL_debug_renderer_info');return e?gl.getParameter(e.UNMASKED_RENDERER_WEBGL):gl.getParameter(gl.RENDERER);});
  assert.deepEqual(report.errors,[]); assert.deepEqual(report.external,[]);
  report.passed=true;
} finally {
  await writeFile(join(output,'browser.json'),JSON.stringify(report,null,2));
  await browser.close();
}
