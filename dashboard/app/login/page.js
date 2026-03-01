'use client';
import { useState } from 'react';

export default function LoginPage() {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('admin123');
  const [error, setError] = useState('');

  async function onSubmit(e) {
    e.preventDefault();
    setError('');
    const resp = await fetch(`${process.env.NEXT_PUBLIC_MCP_BASE_URL}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!resp.ok) {
      setError('登录失败');
      return;
    }
    const data = await resp.json();
    localStorage.setItem('token', data.access_token);
    window.location.href = '/overview';
  }

  return (
    <div>
      <h1>Welike 招募 Dashboard 登录</h1>
      <form onSubmit={onSubmit}>
        <input value={username} onChange={(e)=>setUsername(e.target.value)} placeholder="用户名" /><br />
        <input value={password} type="password" onChange={(e)=>setPassword(e.target.value)} placeholder="密码" /><br />
        <button type="submit">登录</button>
      </form>
      {error && <p style={{color:'red'}}>{error}</p>}
    </div>
  );
}
