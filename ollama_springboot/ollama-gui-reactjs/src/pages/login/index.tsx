import { FormEvent, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { getSession, login } from '~/services/auth'
import ROUTES from '~/constants/routes'
import './login.css'

export default function Login () {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()
  if (getSession()) return <Navigate replace to={ROUTES.ROOT} />
  async function submit (event : FormEvent) {
    event.preventDefault()
    setError(''); setBusy(true)
    try { await login(username, password); navigate(ROUTES.ROOT, { replace: true }) }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Sign-in failed') }
    finally { setBusy(false) }
  }
  return (
    <main className='loginPage'>
      <section className='loginIntro'>
        <span className='loginEyebrow'>SOFTWARE RECO</span>
        <h1>你的问题。<br />你的工作空间。</h1>
        <p>登录后继续软件选型、技术问答和方案讨论。</p>
      </section>
      <form className='loginForm' onSubmit={submit}>
        <h2>登录 / Sign in</h2>
        <label htmlFor='username'>用户名 / Username</label>
        <input autoComplete='username' id='username' maxLength={80} onChange={e => setUsername(e.target.value)} required value={username} />
        <label htmlFor='password'>密码 / Password</label>
        <input autoComplete='current-password' id='password' maxLength={72} onChange={e => setPassword(e.target.value)} required type='password' value={password} />
        {error && <p className='loginError' role='alert'>{error}</p>}
        <button disabled={busy} type='submit'>{busy ? '正在登录…' : '进入工作空间 →'}</button>
      </form>
    </main>
  )
}
