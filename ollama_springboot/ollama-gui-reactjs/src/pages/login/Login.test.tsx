import '@testing-library/jest-dom'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import Login from './index'
import RequireAuth from '~/components/RequireAuth'
import { getSession, setSession } from '~/services/auth'
import ROUTES from '~/constants/routes'

beforeEach(() => { setSession(null); global.fetch = jest.fn() })
afterEach(() => setSession(null))

test('login presents errors and sends the user into a validated workspace', async () => {
  const user = { id: 'user-a', username: 'alice' }
  ;(fetch as jest.Mock).mockResolvedValueOnce({ status: 401, ok: false })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ user, access_token: 'token', token_type: 'Bearer', expires_in: 1800 }) })
    .mockResolvedValueOnce({ ok: true, status: 200, json: async () => user })
  render(<MemoryRouter initialEntries={['/login']}><Routes>
    <Route element={<Login />} path='/login' />
    <Route element={<RequireAuth><p>Private workspace</p></RequireAuth>} path={ROUTES.ROOT} />
  </Routes></MemoryRouter>)
  fireEvent.change(screen.getByLabelText('用户名 / Username'), { target: { value: 'alice' } })
  fireEvent.change(screen.getByLabelText('密码 / Password'), { target: { value: 'bad-password' } })
  fireEvent.click(screen.getByRole('button'))
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid username or password')
  fireEvent.change(screen.getByLabelText('密码 / Password'), { target: { value: 'correct-password' } })
  fireEvent.click(screen.getByRole('button'))
  await waitFor(() => expect(screen.getByText('Private workspace')).toBeInTheDocument())
  expect(getSession()?.user.username).toBe('alice')
})

test('anonymous users cannot mount the private workspace', () => {
  render(<MemoryRouter initialEntries={[ROUTES.ROOT]}><Routes>
    <Route element={<Login />} path='/login' />
    <Route element={<RequireAuth><p>Private workspace</p></RequireAuth>} path={ROUTES.ROOT} />
  </Routes></MemoryRouter>)
  expect(screen.queryByText('Private workspace')).not.toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '登录 / Sign in' })).toBeInTheDocument()
})
