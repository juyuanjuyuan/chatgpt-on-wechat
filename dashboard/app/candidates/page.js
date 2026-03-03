'use client';
import { useEffect, useState } from 'react';
import Nav from '../../components/nav';

export default function Candidates(){
  const [data,setData]=useState({items:[]});
  useEffect(()=>{(async()=>{
    const token=localStorage.getItem('token');
    const r=await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/candidates`,{headers:{Authorization:`Bearer ${token}`}});
    if(r.status===401){window.location.href='/login';return;}
    setData(await r.json());
  })()},[]);
  return <div><Nav/><h1>候选人列表</h1>
    <table border='1' cellPadding='8'><thead><tr><th>ID</th><th>昵称</th><th>城市</th><th>状态</th></tr></thead>
    <tbody>{data.items.map(i=><tr key={i.id}><td><a href={`/candidates/${i.id}`}>{i.id}</a></td><td>{i.nickname}</td><td>{i.city}</td><td>{i.status}</td></tr>)}</tbody></table>
  </div>
}
