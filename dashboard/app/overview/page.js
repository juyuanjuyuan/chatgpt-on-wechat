'use client';
import { useEffect, useState } from 'react';
import Nav from '../../components/nav';

export default function Overview(){
  const [m,setM]=useState(null);
  useEffect(()=>{(async()=>{
    const token=localStorage.getItem('token');
    const r=await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/metrics/overview`,{headers:{Authorization:`Bearer ${token}`}});
    if(r.status===401){window.location.href='/login';return;}
    setM(await r.json());
  })()},[]);
  return <div><Nav/><h1>概览</h1>{m? <div>
    <p>今日新增：{m.today_new_candidates}</p>
    <p>今日发照人数：{m.today_photo_candidates}</p>
    <p>发照转化率（历史）：{m.photo_conversion_rate ?? m.today_photo_conversion_rate}%</p>
  </div>:'loading...'}</div>
}
