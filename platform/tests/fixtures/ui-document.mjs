// DOM boundary for logic tests; layout and browser behavior require a browser.
export function uiDocument(ids=[]){
 class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.attributes={};this.listeners={};this.hidden=false;this.disabled=false;this.value='';this._text='';}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>typeof c==='string'?c:c.textContent).join('');}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this._text='';this.children=[...nodes];}
  setAttribute(key,value){this.attributes[key]=String(value);}
  addEventListener(name,fn){this.listeners[name]=fn;}
  async dispatch(name){await this.listeners[name]?.({target:this});}
 }
 const elements=Object.fromEntries(ids.map(id=>[id,new Element('div')]));
 return {elements,createElement:tag=>new Element(tag),getElementById:id=>elements[id]??null};
}
export function descendants(element){return [element,...element.children.filter(c=>typeof c!=='string').flatMap(descendants)];}
