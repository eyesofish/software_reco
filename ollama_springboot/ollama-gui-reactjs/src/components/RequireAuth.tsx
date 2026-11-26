import { ReactNode, useEffect, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { getSession, subscribeAuth, validateSession } from '~/services/auth'
import { disChats } from '~/stores/chats'
import { loadChats } from '~/stores/chats/actions'
import Store from '~/services/store'

export default function RequireAuth ({ children } : { children : ReactNode }) {
  const [session, setCurrent] = useState(getSession)
  const [validated, setValidated] = useState(false)
  useEffect(() => subscribeAuth(() => {
    disChats(loadChats([]))
    setValidated(false)
    setCurrent(getSession())
  }), [])
  useEffect(() => {
    let active = true
    if (!session) return
    validateSession().then(ok => {
      if (!active || !ok) return
      disChats(loadChats(Store.get('chats') || []))
      setValidated(true)
    })
    return () => { active = false; disChats(loadChats([])) }
  }, [session])
  if (!session) return <Navigate replace to='/login' />
  if (!validated) return <p role='status'>Checking session…</p>
  return <>{children}</>
}
