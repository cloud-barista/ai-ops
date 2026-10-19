import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
export const localModel={model_id:'google/bert_uncased_L-2_H-128_A-2',model_revision:'30b0a37ccaaa32f332884b96992754e246e48c5f',runtime_engine:'PYTORCH',precision:'FP32',max_concurrency:1};
export const localModels=[{...localModel,batch_size:1,max_length:64,label:'BERT Tiny · 배치 1 · 길이 64'},{...localModel,batch_size:8,max_length:128,label:'BERT Tiny · 배치 8 · 길이 128'}];
const workspace=fileURLToPath(new URL('../../',import.meta.url));
export const aiPython=resolve(workspace,'output/khu-platform-validation/ai-venv',process.platform==='win32'?'Scripts/python.exe':'bin/python');
export const aiCache=resolve(workspace,'output/khu-platform-validation/ai-models');
export function assertLocalModel(model){if(!localModels.some(m=>['model_id','model_revision','runtime_engine','precision','batch_size','max_length','max_concurrency'].every(k=>m[k]===model?.[k])))throw Error('unsupported_local_model');}
