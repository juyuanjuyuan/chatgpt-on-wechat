'use client';
export default function Nav(){
  return <div style={{display:'flex',gap:12,marginBottom:16}}>
    <a href='/overview'>概览</a>
    <a href='/candidates'>候选人</a>
    <a href='/prompts'>Prompt管理</a>
  </div>
}
