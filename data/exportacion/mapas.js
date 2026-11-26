/* Exportación cartográfica local. Datos y estilo corresponden al período elegido. */
(function (global) {
'use strict';
const W=3507,H=2481,INK='#183c33',MUTED='#596a65';
const COLORS=['#2892c7','#a0c29b','#fafa64','#fa8d34','#e81014'];
const ORDER=['BABAHOYO','BABA','MONTALVO','PUEBLOVIEJO','QUEVEDO','URDANETA','VENTANAS','VINCES','PALENQUE','BUENA FE','VALENCIA','MOCACHE','QUINSALOMA'];
let territoryPromise,fontPromise;
function fonts(){
 if(typeof FontFace==='undefined'||typeof document==='undefined')return Promise.resolve();
 return fontPromise||(fontPromise=Promise.all([
  ['Regular',{}],['Bold',{weight:'bold'}],['Italic',{style:'italic'}]
 ].map(async ([name,style])=>{const face=new FontFace('MonitorMapa','url(data/exportacion/Mapa-'+name+'.ttf)',style);await face.load();document.fonts.add(face);})).catch(e=>{fontPromise=null;throw Error('No se pudo cargar la tipografía del mapa.');}));
}
function territory(){return territoryPromise||(territoryPromise=fetch('data/exportacion/territorio.json?v=1',{cache:'no-cache'}).then(r=>{if(!r.ok)throw Error('No se pudo cargar la cartografía de exportación.');return r.json();}).catch(e=>{territoryPromise=null;throw e;}));}
function image(url){return new Promise((resolve,reject)=>{const im=new Image();im.onload=()=>resolve(im);im.onerror=()=>reject(Error('No se pudo cargar una imagen del mapa.'));im.src=url;});}
function forecast(data,source){return data.tipo==='pronostico'||['wrf','ecmwf'].includes(source);}
function titleCase(s){return String(s).toLocaleLowerCase('es').replace(/(^|\s)(\p{L})/gu,(_,a,b)=>a+b.toLocaleUpperCase('es'));}
function mm(v){return typeof v==='number'&&Number.isFinite(v)?v.toFixed(2):'—';}
function text(ctx,value,x,y,size=25,{bold=false,italic=false,align='left',color=INK,maxWidth,halo=false}={}){
 ctx.save();ctx.fillStyle=color;ctx.textAlign=align;ctx.textBaseline='middle';
 ctx.font=(italic?'italic ':'')+(bold?'bold ':'')+size+'px MonitorMapa, Arial, sans-serif';
 if(maxWidth){while(ctx.measureText(value).width>maxWidth&&size>12){size-=.5;ctx.font=(italic?'italic ':'')+(bold?'bold ':'')+size+'px MonitorMapa, Arial, sans-serif';}}
 if(halo){ctx.strokeStyle='rgba(255,255,255,.85)';ctx.lineWidth=4;ctx.strokeText(value,x,y);}
 ctx.fillText(value,x,y);ctx.restore();
}
function line(ctx,x0,y0,x1,y1,color='#9aa9a3',width=1){ctx.beginPath();ctx.moveTo(x0,y0);ctx.lineTo(x1,y1);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();}
// WGS84, UTM zone 17 South. Compared against PROJ for the regional extent.
function utm(lon,lat){
 const a=6378137,e2=.0066943799901413165,ep=e2/(1-e2),k=.9996,rad=Math.PI/180;
 const p=lat*rad,l=(lon+81)*rad,s=Math.sin(p),c=Math.cos(p),t=Math.tan(p),N=a/Math.sqrt(1-e2*s*s),T=t*t,C=ep*c*c,A=c*l;
 const M=a*((1-e2/4-3*e2**2/64-5*e2**3/256)*p-(3*e2/8+3*e2**2/32+45*e2**3/1024)*Math.sin(2*p)+(15*e2**2/256+45*e2**3/1024)*Math.sin(4*p)-35*e2**3/3072*Math.sin(6*p));
 return [500000+k*N*(A+(1-T+C)*A**3/6+(5-18*T+T*T+72*C-58*ep)*A**5/120),10000000+k*(M+N*t*(A*A/2+(5-T+9*C+4*C*C)*A**4/24+(61-58*T+T*T+600*C-330*ep)*A**6/720))];
}
function geometryPath(ctx,g,toPixel){
 const poly=p=>{for(const ring of p){ring.forEach((pt,i)=>{const [x,y]=toPixel(pt);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.closePath();}};
 if(g.type==='Polygon')poly(g.coordinates);else if(g.type==='MultiPolygon')g.coordinates.forEach(poly);
}
function strokeGeom(ctx,g,toPixel,color,width){ctx.beginPath();geometryPath(ctx,g,toPixel);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();}
function dateHuman(value,utc=false){return new Intl.DateTimeFormat('es-EC',{timeZone:utc?'UTC':'America/Guayaquil',day:'numeric',month:'long',year:'numeric'}).format(new Date(value));}
function timeHuman(value,utc=false){return new Intl.DateTimeFormat('es-EC',{timeZone:utc?'UTC':'America/Guayaquil',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(value)).replace(':','h');}
function period(data,source){
 let start=data.inicio,end=data.fin;
 if(source==='wrf'){
  const day=String(data.fecha_servicio||'').slice(0,10);if(!/^\d{4}-\d{2}-\d{2}$/.test(day))throw Error('WRF sin fecha válida.');
  start=day+'T07:00:00-05:00';end=new Date(Date.parse(start)+86400000).toISOString();
 }
 if(!Number.isFinite(Date.parse(start))||!Number.isFinite(Date.parse(end)))throw Error('El producto no informa un período válido.');
 const utc=source==='chirps';
 let result='Del '+dateHuman(start,utc)+', '+timeHuman(start,utc)+', al '+dateHuman(end,utc)+', '+timeHuman(end,utc)+(utc?' · UTC':' · Ecuador');
 if(source==='pdir'&&data.intervalos<24)result+=' · Parcial: '+data.intervalos+'/24 h';
 return result;
}
function triangle(ctx,img,src,dst){
 const [a,b,c]=src,[A,B,C]=dst;
 const det=(b[0]-a[0])*(c[1]-a[1])-(c[0]-a[0])*(b[1]-a[1]);if(Math.abs(det)<1e-9)return;
 const m0=((B[0]-A[0])*(c[1]-a[1])-(C[0]-A[0])*(b[1]-a[1]))/det;
 const m2=((C[0]-A[0])*(b[0]-a[0])-(B[0]-A[0])*(c[0]-a[0]))/det;
 const m1=((B[1]-A[1])*(c[1]-a[1])-(C[1]-A[1])*(b[1]-a[1]))/det;
 const m3=((C[1]-A[1])*(b[0]-a[0])-(B[1]-A[1])*(c[0]-a[0]))/det;
 const center=[(A[0]+B[0]+C[0])/3,(A[1]+B[1]+C[1])/3];
 ctx.save();ctx.beginPath();dst.forEach((p,i)=>{const dx=p[0]-center[0],dy=p[1]-center[1],length=Math.hypot(dx,dy)||1;const x=p[0]+dx/length*.2,y=p[1]+dy/length*.2;if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});ctx.closePath();ctx.clip();
 ctx.transform(m0,m1,m2,m3,A[0]-m0*a[0]-m2*a[1],A[1]-m1*a[0]-m3*a[1]);ctx.drawImage(img,0,0);ctx.restore();
}
function rain(ctx,img,bounds,toPixel){
 const [[south,west],[north,east]]=bounds,nx=12,ny=16;
 const point=(i,j)=>{const u=img.width*i/nx,v=img.height*j/ny;return {s:[u,v],d:toPixel(utm(west+(east-west)*i/nx,north+(south-north)*j/ny))};};
 ctx.save();ctx.globalAlpha=.84;ctx.imageSmoothingEnabled=true;ctx.imageSmoothingQuality='high';
 for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){
  const a=point(i,j),b=point(i+1,j),c=point(i+1,j+1),d=point(i,j+1);
  triangle(ctx,img,[a.s,b.s,c.s],[a.d,b.d,c.d]);triangle(ctx,img,[a.s,c.s,d.s],[a.d,c.d,d.d]);
 }
 ctx.restore();
}
function numericLegend(ctx,legend,rect){
 if(!legend||!Array.isArray(legend.rangos)||!Array.isArray(legend.colores))throw Error('Falta la leyenda del período seleccionado.');
 text(ctx,'PRECIPITACIÓN ACUMULADA (mm)',rect.x,rect.y,35,{bold:true,maxWidth:rect.w});
 const step=72;
 legend.rangos.forEach((label,i)=>{const y=rect.y+90+i*step;ctx.fillStyle=/^#[0-9a-f]{6}$/i.test(legend.colores[i])?legend.colores[i]:COLORS[i%5];ctx.fillRect(rect.x,y-23,77,45);ctx.strokeStyle='#89978d';ctx.lineWidth=1;ctx.strokeRect(rect.x,y-23,77,45);text(ctx,label,rect.x+113,y,35);});
}
function symbols(ctx,x,y,w,center=false){
 text(ctx,'SÍMBOLOS'+(center?'':' CONVENCIONALES'),center?x+w/2:x,y,35,{bold:true,align:center?'center':'left',maxWidth:w});
 const left=center?x+w*.08:x;
 line(ctx,left,y+90,left+70,y+90,'#344e45',2.5);text(ctx,'Límite provincial',left+105,y+90,30,{maxWidth:w-110});
 line(ctx,left,y+180,left+70,y+180,'#697f77',1.3);text(ctx,'Límite cantonal',left+105,y+180,30,{maxWidth:w-110});
}
async function render({data,source,scope='area',canvasFactory}={}){
 if(!data||data.unidad!=='mm'||!['imerg','pdir','chirps','wrf','ecmwf'].includes(source))throw Error('Selecciona un producto disponible.');
 if(!Array.isArray(data.bounds)||!data.imagen)throw Error('El producto no tiene imagen cartográfica.');
 const pred=forecast(data,source);await fonts();const t=await territory();
 const [back,img]=await Promise.all([image(t.relieve.imagen),image(data.imagen+'?generado='+encodeURIComponent(data.generado||''))]);
 const canvas=canvasFactory?canvasFactory(W,H):document.createElement('canvas');canvas.width=W;canvas.height=H;const ctx=canvas.getContext('2d');
 ctx.fillStyle='white';ctx.fillRect(0,0,W,H);
 const model=source==='wrf'?'WRF - INAMHI':'ECMWF IFS';
 text(ctx,pred?'PRONÓSTICO DE PRECIPITACIÓN · '+model:'PRECIPITACIÓN ACUMULADA ESTIMADA',W*.035,H*.037,71,{bold:true,maxWidth:W*.93});
 text(ctx,scope==='losrios'?'PROVINCIA DE LOS RÍOS':'ÁREA DE MONITOREO · PROVINCIA DE LOS RÍOS Y TERRITORIOS ALEDAÑOS',W*.035,H*.074,42,{color:MUTED,maxWidth:W*.93});
 text(ctx,period(data,source),W*.035,H*.106,42,{bold:true,maxWidth:W*.93});
 line(ctx,W*.035,H*.129,W*.965,H*.129,INK,3);
 const map={x:W*.035,y:H*.153,w:W*(pred?.735:.635),h:H*.745};
 const raw=scope==='losrios'?t.bounds_losrios:t.bounds_area,cx=(raw[0]+raw[2])/2,cy=(raw[1]+raw[3])/2;
 let height=(raw[3]-raw[1])*1.12,width=height*map.w/map.h;
 if(width<(raw[2]-raw[0])*1.10){width=(raw[2]-raw[0])*1.10;height=width*map.h/map.w;}
 const extent=[cx-width/2,cx+width/2,cy-height/2,cy+height/2];
 const px=p=>[map.x+(p[0]-extent[0])/width*map.w,map.y+(extent[3]-p[1])/height*map.h];
 ctx.save();ctx.beginPath();ctx.rect(map.x,map.y,map.w,map.h);ctx.clip();ctx.fillStyle='#f3f5f2';ctx.fillRect(map.x,map.y,map.w,map.h);
 const bg=t.relieve.extent,a=px([bg[0],bg[3]]),b=px([bg[1],bg[2]]);ctx.drawImage(back,a[0],a[1],b[0]-a[0],b[1]-a[1]);
 ctx.save();if(scope==='losrios'){ctx.beginPath();geometryPath(ctx,t.losrios,px);ctx.clip('evenodd');}
 rain(ctx,img,data.bounds,px);ctx.restore();
 for(const f of t.cantones)strokeGeom(ctx,f.geometry,px,'#697f77',.8);
 for(const p of t.provincias)strokeGeom(ctx,p.geometry,px,'#344e45',1.6);
 strokeGeom(ctx,t.losrios,px,'#164d38',2.6);
 for(const f of t.cantones){if(f.provincia!=='LOS RÍOS')continue;const [x,y]=px(f.label);ctx.save();ctx.translate(x,y);if(['PUEBLOVIEJO','QUINSALOMA'].includes(f.canton))ctx.rotate(-Math.PI/2);text(ctx,f.canton,0,0,17.9,{align:'center',halo:true});ctx.restore();}
 for(const p of t.provincias){if(p.nombre==='LOS RÍOS')continue;const [x,y]=px(p.label);const labels=p.nombre==='SANTO DOMINGO DE LOS TSÁCHILAS'?['Santo Domingo','de los Tsáchilas']:[titleCase(p.nombre)];labels.forEach((value,i)=>text(ctx,value,x,y+(i-(labels.length-1)/2)*29,26.6,{align:'center',italic:true,halo:true}));}
 const nx=map.x+map.w*.94,ny=map.y+map.h*.085;
 text(ctx,'N',nx,ny-55,50,{bold:true,align:'center'});line(ctx,nx,ny+130,nx,ny-20,INK,3);
 ctx.fillStyle=INK;ctx.beginPath();ctx.moveTo(nx,ny-40);ctx.lineTo(nx-20,ny+7);ctx.lineTo(nx+20,ny+7);ctx.closePath();ctx.fill();
 const distance=scope==='losrios'?25000:50000,segment=distance/5,scalePx=distance/width*map.w,sx=map.x+map.w*.05,sy=map.y+map.h*.94;
 for(let i=0;i<5;i++){ctx.fillStyle=i%2?'white':INK;ctx.fillRect(sx+scalePx*i/5,sy,scalePx/5,15);ctx.strokeStyle=INK;ctx.lineWidth=1;ctx.strokeRect(sx+scalePx*i/5,sy,scalePx/5,15);}
 for(let i=0;i<=5;i++)text(ctx,String(segment*i/1000),sx+scalePx*i/5,sy+45,24,{align:'center'});
 text(ctx,'km',sx+scalePx+35,sy+8,26);ctx.restore();
 ctx.strokeStyle='#788b80';ctx.lineWidth=1.6;ctx.strokeRect(map.x,map.y,map.w,map.h);
 const sx2=W*(pred?.800:.700),sw=W*(pred?.165:.265);
 if(pred){
  text(ctx,'Precipitación prevista',sx2+sw/2,map.y+map.h*.368,37,{align:'center',maxWidth:sw});
  const x=sx2+sw*.05,y=map.y+map.h*.418,w=sw*.9,h=H*.0225;const gradient=ctx.createLinearGradient(x,0,x+w,0);gradient.addColorStop(0,'#2892c7');gradient.addColorStop(.5,'#fafa64');gradient.addColorStop(1,'#e81014');ctx.fillStyle=gradient;ctx.fillRect(x,y,w,h);
  [[x,'Baja','left'],[sx2+sw/2,'Media','center'],[x+w,'Alta','right']].forEach(([px,label,align])=>text(ctx,label,px,y+h+45,33,{align}));
  symbols(ctx,sx2,map.y+map.h*.573,sw,true);
 }else{
  text(ctx,'PROVINCIA DE LOS RÍOS',sx2,map.y+15,46,{bold:true,maxWidth:sw});
  text(ctx,'Precipitación estimada por cantón (mm)',sx2,map.y+100,35,{maxWidth:sw});
  const rows=new Map((data.cantones||[]).filter(f=>f.provincia==='LOS RÍOS').map(f=>[f.canton,f]));
  const top=map.y+145,rowH=67,col=[sx2,sx2+sw*.5,sx2+sw*.75,sx2+sw];
  for(let r=0;r<14;r++){const y=top+r*rowH;ctx.fillStyle=r===0?'#e8eeea':r%2===0?'#f7f9f7':'white';ctx.fillRect(sx2,y,sw,rowH);ctx.strokeStyle='#d3dcd7';ctx.lineWidth=.8;ctx.strokeRect(sx2,y,sw,rowH);
   if(r===0){text(ctx,'Cantón',(col[0]+col[1])/2,y+rowH/2,33,{bold:true,align:'center'});text(ctx,'Máxima',(col[1]+col[2])/2,y+rowH/2,33,{bold:true,align:'center'});text(ctx,'Media',(col[2]+col[3])/2,y+rowH/2,33,{bold:true,align:'center'});}
   else{const n=ORDER[r-1],f=rows.get(n)||{};text(ctx,titleCase(n),sx2+22,y+rowH/2,33,{maxWidth:sw*.46});text(ctx,mm(f.maxima_mm),col[2]-25,y+rowH/2,33,{align:'right'});text(ctx,mm(f.media_mm),col[3]-25,y+rowH/2,33,{align:'right'});}
  }
  numericLegend(ctx,data.leyenda,{x:sx2,y:map.y+map.h*.626,w:sw});
  symbols(ctx,sx2,map.y+map.h*.880,sw);
 }
 line(ctx,W*.035,H*.920,W*.965,H*.920,'#b6c3ba',1.6);
 let sourceText=({imerg:'NASA · GPM IMERG Early',pdir:'CHRS · University of California, Irvine · PDIR-Now',chirps:'Climate Hazards Center · CHIRPS',wrf:'WRF · servicio del visor INAMHI',ecmwf:'ECMWF IFS · ECMWF Open Data (CC BY 4.0)'})[source];
 if(source==='pdir')sourceText+=' · '+(data.intervalos===24?'Período completo: 24 horas':'Período parcial: '+data.intervalos+'/24 horas');
 text(ctx,'Fuente: '+sourceText+'.',W*.035,H*.944,29,{color:MUTED,maxWidth:W*.60});
 text(ctx,'Proyección: WGS 84 / UTM 17S · Fondo de relieve: Esri, USGS, NOAA.',W*.035,H*.967,28,{color:MUTED,maxWidth:W*.60});
 text(ctx,'Elaborado por Ing. Agr. Alex Guamán',W*.965,H*.956,33,{bold:true,align:'right'});
 return canvas;
}
function pdf(canvas){
 const data=canvas.toDataURL('image/jpeg',.97).split(',')[1],bin=atob(data),jpg=Uint8Array.from(bin,c=>c.charCodeAt(0));
 const enc=new TextEncoder(),chunks=[],offsets=[0];let length=0;
 const add=v=>{const b=typeof v==='string'?enc.encode(v):v;chunks.push(b);length+=b.length;};
 add('%PDF-1.4\n');
 const obj=(n,s)=>{offsets[n]=length;add(n+' 0 obj\n'+s+'\nendobj\n');};
 obj(1,'<< /Type /Catalog /Pages 2 0 R >>');obj(2,'<< /Type /Pages /Kids [3 0 R] /Count 1 >>');
 obj(3,'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 841.89 595.28] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>');
 offsets[4]=length;add('4 0 obj\n<< /Type /XObject /Subtype /Image /Width '+canvas.width+' /Height '+canvas.height+' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length '+jpg.length+' >>\nstream\n');add(jpg);add('\nendstream\nendobj\n');
 const content='q\n841.89 0 0 595.28 0 0 cm\n/Im0 Do\nQ\n';obj(5,'<< /Length '+enc.encode(content).length+' >>\nstream\n'+content+'endstream');
 const start=length;add('xref\n0 6\n0000000000 65535 f \n');for(let i=1;i<=5;i++)add(String(offsets[i]).padStart(10,'0')+' 00000 n \n');
 add('trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+start+'\n%%EOF');return new Blob(chunks,{type:'application/pdf'});
}
async function crearPNG(canvas){
 const original=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('No se pudo crear el PNG.')),'image/png'));
 const raw=new Uint8Array(await original.arrayBuffer()),view=new DataView(raw.buffer),parts=[raw.slice(0,8)];
 const chunk=new Uint8Array(21),v=new DataView(chunk.buffer);v.setUint32(0,9);chunk.set([112,72,89,115],4);v.setUint32(8,11811);v.setUint32(12,11811);chunk[16]=1;
 let crc=0xffffffff;for(const byte of chunk.slice(4,17)){crc^=byte;for(let j=0;j<8;j++)crc=(crc>>>1)^((crc&1)?0xedb88320:0);}v.setUint32(17,(crc^0xffffffff)>>>0);
 for(let at=8;at<raw.length;){const length=view.getUint32(at),type=String.fromCharCode(...raw.slice(at+4,at+8));if(type!=='pHYs')parts.push(raw.slice(at,at+length+12));if(type==='IHDR')parts.push(chunk);at+=length+12;}
 return new Blob(parts,{type:'image/png'});
}
function filename(data,source,scope){
 let start=data.inicio,end=data.fin;
 if(source==='wrf'){start=data.fecha_servicio.slice(0,10)+'T07:00:00-05:00';end=new Date(Date.parse(start)+86400000).toISOString();}
 const stamp=value=>{const parts=new Intl.DateTimeFormat('en-CA',{timeZone:source==='chirps'?'UTC':'America/Guayaquil',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date(value));const get=k=>parts.find(p=>p.type===k).value;return get('year')+get('month')+get('day')+'_'+get('hour')+get('minute');};
 return 'Mapa_'+source+'_'+(scope==='losrios'?'Los_Rios':'Toda_el_area')+'_'+stamp(start)+'_a_'+stamp(end)+(source==='pdir'?'_'+data.intervalos+'h':'');
}

function install({obtener,fuente,provincia,despues,estado,detalles,selectProvincia,reset}){
 const panel=document.createElement('section');panel.className='panel details';panel.hidden=true;
 panel.innerHTML='<h2>Descargar mapa</h2><label for="mapaAlcance">Área del mapa</label><select id="mapaAlcance"><option value="area">Toda el área</option><option value="losrios">Provincia de Los Ríos</option></select><div style="display:flex;gap:10px;flex-wrap:wrap"><button id="mapaPNG" type="button" style="flex:1">Descargar mapa PNG</button><button id="mapaPDF" type="button" style="flex:1">Descargar mapa PDF</button></div><p id="mapaMensaje" class="subtle" role="status" aria-live="polite"></p>';
 despues.after(panel);const scope=panel.querySelector('#mapaAlcance'),png=panel.querySelector('#mapaPNG'),pdfButton=panel.querySelector('#mapaPDF'),message=panel.querySelector('#mapaMensaje');let busy=false;
 const refresh=()=>{const ok=!!obtener();panel.hidden=!ok;png.disabled=pdfButton.disabled=busy||!ok;scope.disabled=busy;};
 const syncScope=()=>{scope.value=provincia()==='LOS RÍOS'?'losrios':'area';refresh();};
 async function save(format){
  if(busy)return;const data=obtener(),source=fuente(),area=scope.value;if(!data)return;
  busy=true;refresh();message.textContent='Preparando mapa de '+(area==='losrios'?'Los Ríos':'toda el área')+'…';
  try{await new Promise(r=>setTimeout(r,30));const canvas=await render({data,source,scope:area});
   const blob=format==='pdf'?pdf(canvas):await crearPNG(canvas);
   const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=filename(data,source,area)+'.'+format;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);message.textContent='Mapa listo.';
  }catch(e){message.textContent='No se pudo generar el mapa. '+e.message;}
  finally{busy=false;refresh();}
 }
 png.addEventListener('click',()=>save('png'));pdfButton.addEventListener('click',()=>save('pdf'));
 const obs=new MutationObserver(refresh);obs.observe(estado,{childList:true,subtree:true,characterData:true});obs.observe(detalles,{childList:true,subtree:true});
 selectProvincia.addEventListener('change',syncScope);reset.addEventListener('click',syncScope);syncScope();
}
global.MeteoMapas={renderizar:render,pdf,png:crearPNG,instalar:install,utm,periodo:period,nombreArchivo:filename};
})(typeof window!=='undefined'?window:globalThis);
