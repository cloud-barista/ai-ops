import {readFile} from 'node:fs/promises';
import {fault} from './index.mjs';

const [specification,html]=await Promise.all([
  readFile(new URL('../data/openapi.json',import.meta.url),'utf8'),
  readFile(new URL('../swagger.html',import.meta.url),'utf8'),
]);
const document=JSON.parse(specification);
const routes=Object.entries(document.paths).map(([path,methods])=>({
  pattern:new RegExp('^'+path.split(/(\{[^}]+\})/).map(part=>part.startsWith('{')?'[^/]+':part.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('')+'$'),
  methods:Object.keys(methods),
}));

export async function serveSwagger(request,response,clients){
  const url=new URL(request.url,'http://localhost'),path=url.pathname;
  if(['/swagger','/swagger/','/swagger/index.html','/swagger/openapi.json','/openapi.json'].includes(path)){
    if(request.method!=='GET')throw fault(405,'method_not_allowed');
    const spec=path.endsWith('/openapi.json');
    response.writeHead(200,{'content-type':(spec?'application/json':'text/html')+'; charset=utf-8','cache-control':'no-store'});
    response.end(spec?specification:html);return;
  }
  const prefix='/swagger/services';
  if(!path.startsWith(prefix+'/'))throw fault(404,'not_found');
  const targetPath=path.slice(prefix.length),route=routes.find(entry=>entry.pattern.test(targetPath));
  if(!route)throw fault(404,'not_found');
  if(!route.methods.includes(request.method.toLowerCase()))throw fault(405,'method_not_allowed');
  const [,service]=targetPath.split('/'),headers={};
  for(const name of ['content-type','accept','authorization','x-aimcmp-namespace','mcp-protocol-version','mcp-method','mcp-name'])if(request.headers[name])headers[name]=request.headers[name];
  let body;
  if(!['GET','HEAD'].includes(request.method)){
    const parts=[];let size=0;
    for await(const part of request){size+=part.length;if(size>4*1024*1024)throw fault(413,'body_too_large');parts.push(part);}
    body=Buffer.concat(parts);
  }
  let upstream,data;
  try{
    upstream=await clients.swaggerRequest(service,targetPath.slice(service.length+1)+url.search,{method:request.method,headers,body});
    data=Buffer.from(await upstream.arrayBuffer());
  }
  catch{throw fault(502,'swagger_upstream_unavailable');}
  const responseHeaders={'cache-control':'no-store'};
  for(const name of ['content-type','idempotent-replayed','www-authenticate'])if(upstream.headers.has(name))responseHeaders[name]=upstream.headers.get(name);
  response.writeHead(upstream.status,responseHeaders);response.end(data);
}
