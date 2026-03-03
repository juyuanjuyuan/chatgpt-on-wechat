'use client';
import { useEffect, useState } from 'react';
import Nav from '../../components/nav';

export default function Prompts(){
  const [list,setList]=useState([]);
  const [version,setVersion]=useState('v2');
  const [content,setContent]=useState('');
  const load=async()=>{
    const token=localStorage.getItem('token');
    const r=await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/prompts`,{headers:{Authorization:`Bearer ${token}`}});
    if(r.status===401){window.location.href='/login';return;}
    setList(await r.json());
  }
  useEffect(()=>{load()},[]);
  async function publish(){
    const token=localStorage.getItem('token');
    await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/prompts/publish`,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({version,content,published_by:'admin'})});
    await load();
  }
  async function rollback(v){
    const token=localStorage.getItem('token');
    await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/prompts/rollback`,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({version:v})});
    await load();
  }
  return <div><Nav/><h1>Prompt 管理</h1>
    <input value={version} onChange={e=>setVersion(e.target.value)} placeholder='版本'/><br/>
    <textarea value={content} onChange={e=>setContent(e.target.value)} rows={8} cols={80}/><br/>
    <button onClick={publish}>发布新版本</button>
    <ul>{list.map(p=><li key={p.id}>{p.version} {p.is_active?'(生效中)':''} <button onClick={()=>rollback(p.version)}>回滚到此版本</button></li>)}</ul>
  </div>
}
