import RequireAuth from '~/components/RequireAuth'
import Login from '~/pages/login'
import { lazy } from 'react'
import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom'

import ROUTES from '~/constants/routes'

const Chat = lazy(() => import('~/pages/chat'))
const Config = lazy(() => import('~/pages/config'))
const NotFound = lazy(() => import('~/pages/notFound'))
const SplashScreen = lazy(() => import('~/pages/splashScreen'))

const router = createBrowserRouter([
  { path: '/login', element: <Login /> },
  { element: <RequireAuth><Outlet /></RequireAuth>, children: [
  {
    path: '/',
    errorElement: <SplashScreen><NotFound /></SplashScreen>,
    element: <Navigate replace to={ROUTES.ROOT} />
  },
  {
    path: ROUTES.ROOT,
    element: <SplashScreen><Chat /></SplashScreen>
  },
  {
    path: ROUTES.CHAT,
    element: <SplashScreen><Chat /></SplashScreen>
  },
  {
    path: ROUTES.CHAT_INDEX,
    element: <SplashScreen><Chat /></SplashScreen>
  },
  {
    path: ROUTES.CONFIG,
    element: <SplashScreen><Config /></SplashScreen>
  }
  ] }
])

export default router
