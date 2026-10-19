import {readFile,mkdir,writeFile} from 'node:fs/promises';import {createHash} from 'node:crypto';import {fileURLToPath,pathToFileURL} from 'node:url';import {resolve} from 'node:path';
export async function buildVmArtifact(model,output){
 if(model.model_id!=='google/bert_uncased_L-2_H-128_A-2'||model.model_revision!=='30b0a37ccaaa32f332884b96992754e246e48c5f'||model.batch_size!==1||model.max_length!==64)throw Error('cloud_smoke_model_required');
 const source=fileURLToPath(new URL('../runtime-app/bert_worker.py',import.meta.url));const files={'bert_worker.py':await readFile(source,'utf8')};for(const name of ['requirements-cpu.txt','requirements-cuda.txt'])files[name]=await readFile(fileURLToPath(new URL('../runtime-vm/'+name,import.meta.url)),'utf8');
 const manifest={schema_version:1,model:{...model,seed:42,dtype:'float32'},device_scope:'cpu_or_cuda'},payload={manifest,files},sha256=createHash('sha256').update(JSON.stringify(payload)).digest('hex');
 let uri=pathToFileURL(source).href;if(output){await mkdir(output,{recursive:true});const artifact=resolve(output,'bert_worker.py');await writeFile(artifact,files['bert_worker.py'],'utf8');await writeFile(resolve(output,'manifest.json'),JSON.stringify({manifest,sha256},null,2),'utf8');uri=pathToFileURL(artifact).href;}
 return {manifest,payload,sha256,artifact:{type:'SCRIPT',uri}};
}
