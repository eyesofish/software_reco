import styled from 'styled-components'

export const StyledFooter = styled.footer`
  align-items: center;
  background-color: ${({ theme }) => theme.colors.footerBg};
  display: flex;
  justify-content: flex-end;
  padding: 8px;
  width: 100%;
`
