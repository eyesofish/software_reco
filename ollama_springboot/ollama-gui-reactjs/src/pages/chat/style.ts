import styled from 'styled-components'

export const InputContainer = styled.div`
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 90%;
`

export const ComposerRow = styled.div`
  align-items: flex-end;
  display: flex;
  gap: 8px;
  width: 100%;
`

export const AttachButton = styled.button`
  align-items: center;
  background: var(--color-assistant-pre);
  border: 1px solid var(--color-code-border);
  border-radius: 8px;
  color: var(--color-text);
  cursor: pointer;
  display: flex;
  flex: 0 0 44px;
  height: 44px;
  justify-content: center;
`

export const ImagePreviewList = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
`

export const ImagePreview = styled.div`
  background: var(--color-assistant-pre);
  border: 1px solid var(--color-code-border);
  border-radius: 8px;
  max-width: 180px;
  padding: 6px;
  position: relative;

  img {
    border-radius: 6px;
    display: block;
    height: 88px;
    object-fit: cover;
    width: 120px;
  }

  span {
    display: block;
    font-size: 12px;
    max-width: 120px;
    overflow: hidden;
    padding-top: 4px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  button {
    background: rgba(0, 0, 0, 0.72);
    border: 0;
    border-radius: 50%;
    color: white;
    cursor: pointer;
    height: 22px;
    position: absolute;
    right: 2px;
    top: 2px;
    width: 22px;
  }
`

export const ComposerError = styled.p`
  color: #c33;
  font-size: 12px;
  margin: 0;
`

export const Loading = styled.img`
  position: fixed;
  right: 44px;
  top: 22px;
`

export const Talk = styled.div`
  height: 100%;
  line-height: 1.6;
  margin-bottom: 16px;
  overflow-y: auto;
  padding-right: 8px;
  text-align: justify;
  width: 100%;
`