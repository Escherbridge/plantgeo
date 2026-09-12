---
type: reproduction-recipe
status: local-fixture-only
---

# Local renderer fixture reproduction

The following temporary files ran under .omc/research/scalar-canvas. They bundle the actual components through existing esbuild dependencies and serve only 127.0.0.1:3097. Run node .omc/research/scalar-canvas/run.mjs from the repository root, then the pixel-analysis script. Use an installed Playwright Chromium; no packages or fonts are downloaded. The raw weather sourceBytes field was nulled in the receipt because the helper collection is unrelated to that smoke case.

## entry.tsx

```text
import React from 'react';
import {createRoot} from 'react-dom/client';
import maplibregl from 'maplibre-gl';
import {ClimateFieldLayer} from '../../../src/components/map/layers/ClimateFieldLayer';
import {SoilFieldLayer} from '../../../src/components/map/layers/SoilFieldLayer';
import {WeatherLayer} from '../../../src/components/map/layers/WeatherLayer';
import {servedCellLattice, LANE_BASE_LATTICES, tessellatedCellPolygon} from '../../../src/lib/map/zoom-tiers';

const root=createRoot(document.getElementById('root'));
const style=()=>({version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#15222b'}}]});
const errors=[];
const map=new maplibregl.Map({container:'map',style:style(),center:[-120,45],zoom:7,preserveDrawingBuffer:true,fadeDuration:0,attributionControl:false});
map.on('error',e=>errors.push(String(e.error)));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
window.runCase=async ({kind='climate',zoom=7,empty=false,opacity=1,form='field',reload=false})=>{
  const started=performance.now();
  root.render(null); await sleep(80);
  if(reload) {map.setStyle(style()); await sleep(200);}
  const tier=zoom>=13?13:zoom>=9?9:zoom>=5?5:0;
  const lane=kind==='soil'?'soil-field':'climate-field';
  const lattice=servedCellLattice(tier,LANE_BASE_LATTICES[lane]);
  const size=lattice.cellSizeDegrees;
  const center=kind==='soil'?[-119.875,45.125]:[-120,45];
  map.jumpTo({center,zoom});
  const values=kind==='soil'?[0,0.18,0.31]:[0,12.5,24];
  const features=[-1,0,1].map((delta,i)=>({type:'Feature',id:i,geometry:tessellatedCellPolygon(center[0]+delta*size,center[1],lattice),properties:{value:values[i],aggregated:tier!==13,coverageFraction:1,bandLabel:'10 to 15',unit:kind==='soil'?'m3/m3':'Cel'}}));
  const geojson={type:'FeatureCollection',features:empty?[]:features};
  if(kind==='soil') root.render(<SoilFieldLayer map={map} measure="moisture" geojson={geojson} opacityScale={opacity}/>);
  else if(kind==='weather') root.render(<WeatherLayer map={map} opacityScale={opacity} data={empty?[]:[{coordinates:center,temperature:12.5,windSpeed:3.2,windDirection:90,humidity:67,precipitation:0,sampleKind:'model_estimate'}]}/>);
  else root.render(<ClimateFieldLayer map={map} signal="air-temperature" renderForm={form} zoomTier={tier} geojson={geojson} opacityScale={opacity}/>);
  await sleep(700);
  await new Promise(resolve=>{if(map.loaded())resolve();else map.once('idle',resolve)});
  await sleep(150);
  const layers=map.getStyle().layers;
  const labelIds=layers.filter(l=>l.type==='symbol').map(l=>l.id);
  const canvas=map.getCanvas();
  const gl=canvas.getContext('webgl2')||canvas.getContext('webgl');
  const pixels=new Uint8Array(canvas.width*canvas.height*4); gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
  let bright=0;for(let i=0;i<pixels.length;i+=4) if(pixels[i]>200&&pixels[i+1]>200&&pixels[i+2]>200)bright++;
  return {kind,zoom,tier,empty,opacity,form,reload,fixture:true,sourceFeatureCount:geojson.features.length,sourceBytes:JSON.stringify(geojson).length,labelIds,renderedLabelCount:map.queryRenderedFeatures(undefined,{layers:labelIds}).length,brightPixels:bright,errors:[...errors],renderAndSettleMs:Math.round(performance.now()-started),size,lattice};
};
window.ready=true;


```

## index.html

```text
<!doctype html><html><head><meta charset="utf-8"><style>html,body,#map{margin:0;width:100%;height:100%;overflow:hidden}#map{position:absolute}.maplibregl-canvas{position:absolute}#title{position:absolute;z-index:2;top:12px;left:14px;background:#15222b;color:#fff;font:14px Arial;padding:9px}#root{position:absolute}</style></head><body><div id="map"></div><div id="root"></div><div id="title">Synthetic renderer fixture • no live observations or basemap</div><script src="/bundle.js"></script></body></html>

```

## run.mjs

```text
import {build} from 'esbuild';
import {chromium} from '@playwright/test';
import {createServer} from 'node:http';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
const dir=path.resolve('.omc/research/scalar-canvas');
await build({entryPoints:[path.join(dir,'entry.tsx')],outfile:path.join(dir,'bundle.js'),bundle:true,platform:'browser',jsx:'automatic',define:{'process.env.NODE_ENV':'"production"'},alias:{'@':path.resolve('src')}});
const server=createServer(async(req,res)=>{const filename=req.url==='/bundle.js'?'bundle.js':'index.html';res.setHeader('Content-Type',filename.endsWith('.js')?'text/javascript':'text/html');res.end(await readFile(path.join(dir,filename)));});
await new Promise(r=>server.listen(3097,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const output=path.resolve('conductor/tracks/multiscale_polygon_surface_20260901/evidence/scalar-labels-20260912');
await mkdir(output,{recursive:true});
const results=[];
try{
for(const viewport of [{width:1280,height:720},{width:390,height:844}]){
 const page=await browser.newPage({viewport,deviceScaleFactor:1});
 const pageErrors=[];page.on('pageerror',e=>pageErrors.push(String(e)));
 await page.route('**/*',r=>r.request().url().startsWith('http://127.0.0.1:3097/')?r.continue():r.abort());
 await page.goto('http://127.0.0.1:3097/');await page.waitForFunction(()=>window.ready);
 const cases=[...['climate','soil'].flatMap(kind=>[3,7,10,13].map(zoom=>({kind,zoom}))),{kind:'climate',zoom:7,empty:true},{kind:'soil',zoom:10,opacity:0},{kind:'climate',zoom:7,reload:true},{kind:'climate',zoom:7,form:'isoline'},{kind:'weather',zoom:10}];
 for(const scenario of cases){
 const result=await page.evaluate(x=>window.runCase(x),scenario);
 const name=`${viewport.width}-${scenario.kind}-z${scenario.zoom}${scenario.empty?'-empty':''}${scenario.opacity===0?'-opacity-zero':''}${scenario.reload?'-reload':''}${scenario.form?'-'+scenario.form:''}`;
 await page.locator('#title').evaluate((el,text)=>el.textContent=text,`SYNTHETIC ${scenario.kind} | z${scenario.zoom} | ${viewport.width}px | no live data`);
 await page.screenshot({path:path.join(output,name+'.png')});
 results.push({...result,viewport,pageErrors:[...pageErrors],screenshot:name+'.png'});
 console.log(JSON.stringify({name,labels:result.renderedLabelCount,errors:result.errors,pageErrors}));
 }
 await page.close();
}
await writeFile(path.join(output,'canvas-report.json'),JSON.stringify(results,null,2)+'\n');
}finally{await browser.close();server.close();}

```

## pixels.mjs

```text
import {chromium} from '@playwright/test';
import {readFile,writeFile} from 'node:fs/promises';
const dir='conductor/tracks/multiscale_polygon_surface_20260901/evidence/scalar-labels-20260912/';
const report=JSON.parse(await readFile(dir+'canvas-report.json','utf8'));
const browser=await chromium.launch({headless:true});const page=await browser.newPage();
try{for(const row of report){
const bytes=await readFile(dir+row.screenshot);
Object.assign(row,await page.evaluate(async data=>{
 const img=new Image();img.src='data:image/png;base64,'+data;await img.decode();
 const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;
 const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0);const pixels=ctx.getImageData(0,0,img.width,img.height).data;
 let bright=0,background=0;for(let i=60*img.width*4;i<pixels.length;i+=4){if(pixels[i]>220&&pixels[i+1]>220&&pixels[i+2]>220)bright++;if(pixels[i]===21&&pixels[i+1]===34&&pixels[i+2]===43)background++;}
 const gaps=(y,start,end)=>{let n=0;for(let x=start;x<=end;x++){const i=(y*img.width+x)*4;if(pixels[i]===21&&pixels[i+1]===34&&pixels[i+2]===43)n++;}return n;};
 return {screenshotBrightPixels:bright,screenshotBackgroundPixels:background,screenshotSampledPixels:(img.height-60)*img.width,seamProbe:img.width===1280?{row:320,start:370,end:910,backgroundPixels:gaps(320,370,910)}:{row:405,start:127,end:262,backgroundPixels:gaps(405,127,262)},readPixelsReliable:false};
},bytes.toString('base64')));
}
await writeFile(dir+'canvas-report.json',JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report.filter(x=>x.empty||x.opacity===0||x.screenshot==='1280-climate-z7.png'||x.screenshot==='390-soil-z7.png').map(x=>({file:x.screenshot,bright:x.screenshotBrightPixels,background:x.screenshotBackgroundPixels,sampled:x.screenshotSampledPixels,seam:x.seamProbe})),null,2));
}finally{await browser.close();}

```
