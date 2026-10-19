import {readFile} from 'node:fs/promises';
export async function loadVmCatalog({fallbackEnabled=process.env.KHU_CLOUD_GPU_FALLBACK_ENABLED==='true'}={}){
 const base=JSON.parse(await readFile(new URL('../data/khu-vm-spec-catalog.json',import.meta.url),'utf8'));
 if(!fallbackEnabled)return base;
 const extra=JSON.parse(await readFile(new URL('../data/khu-vm-spec-fallbacks.json',import.meta.url),'utf8'));
 if(extra.entries.length!==1||extra.entries[0].name!=='g5.xlarge'||base.entries.some(s=>s.name===extra.entries[0].name))throw Error('invalid_fallback_catalog');
 return {...base,entries:[...base.entries,...extra.entries]};
}
