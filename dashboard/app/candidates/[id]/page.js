'use client';
import { useEffect, useState } from 'react';
import Nav from '../../../components/nav';

export default function CandidateDetail({params}){
  const [d,setD]=useState(null);
  const [status,setStatus]=useState('reviewing');
  useEffect(()=>{(async()=>{
    const token=localStorage.getItem('token');
    const r=await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/candidates/${params.id}`,{headers:{Authorization:`Bearer ${token}`}});
    if(r.status===401){window.location.href='/login';return;}
    const j=await r.json(); setD(j); setStatus(j.candidate.status);
  })()},[params.id]);
  async function save(){
    const token=localStorage.getItem('token');
    await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/candidates/${params.id}/status`,{method:'PATCH',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:JSON.stringify({status})});
    alert('已更新');
  }
  if(!d)return <div>loading</div>;
  return <div><Nav/><h1>候选人详情 #{params.id}</h1>
    <p>昵称：{d.candidate.nickname} 城市：{d.candidate.city}</p>
    <select value={status} onChange={e=>setStatus(e.target.value)}>
      {['pending_photo','pending_review','reviewing','passed','rejected','blacklisted','underage_terminated','need_more_photo'].map(s=><option key={s} value={s}>{s}</option>)}
    </select><button onClick={save}>更新状态</button>
    <h3>对话时间线</h3>
    <ul>{d.messages.map(m=><li key={m.id}>[{m.sender}] {m.message_type}: {m.content}</li>)}</ul>
    <h3>照片墙（点击加载原图）</h3>
    <ul>{d.photos.map(p=><li key={p.id}><a target='_blank' href={`${process.env.NEXT_PUBLIC_MCP_BASE_URL}${p.preview_url}`}>{p.filename}</a></li>)}</ul>
  </div>
}
