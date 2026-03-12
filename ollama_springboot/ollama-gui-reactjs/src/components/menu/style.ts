import { Link } from 'react-router-dom'
import styled from 'styled-components'

export const ButtonsContainer = styled.div`
  display: flex;
  height: fit-content;
  justify-content: space-between;
  padding: 8px;
  width: 100%;
`

export const ChatRow = styled.div`
  align-items: center;
  display: flex;
  gap: 8px;
  margin-bottom: 4px;
  width: 100%;

  .delete-btn {
    opacity: 0;
  }

  &:hover .delete-btn {
    opacity: 1;
  }
`

export const Chat = styled(Link)`
  border-radius: 4px;
  color: ${({ theme }) => theme.colors.menuText};
  cursor: pointer;
  display: block;
  flex: 1;
  overflow: hidden;
  padding: 4px;
  text-overflow: ellipsis;
  transition: 0.3s;
  white-space: nowrap;
  width: 100%;

  &:hover {
    background-color: ${({ theme }) => theme.colors.menuHoverBg};
    box-shadow: 0 0 0 4px ${({ theme }) => theme.colors.accentFocus};
    color: ${({ theme }) => theme.colors.menuTextActive};
  }
`

export const DeleteButton = styled.button`
  background: transparent;
  border: none;
  color: ${({ theme }) => theme.colors.menuText};
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
  min-width: 18px;
  padding: 0 4px;
  transition: color 0.2s ease, opacity 0.2s ease;

  &:hover {
    color: ${({ theme }) => theme.colors.menuTextActive};
  }
`

export const ChatsContainer = styled.div`
  height: 100%;
  overflow-y: auto;
  padding: 8px;
  width: 100%;
`

export const Container = styled.div<{ $isMobile : boolean }>`
  background-color: ${({ theme }) => theme.colors.menuBg};
  border-right: 1px solid ${({ theme }) => theme.colors.menuBorder};
  box-shadow: ${({ theme }) => theme.colors.menuShadow};
  color: ${({ theme }) => theme.colors.menuText};
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: ${({ $isMobile }) => $isMobile ? '100vw' : '20%'};
`
