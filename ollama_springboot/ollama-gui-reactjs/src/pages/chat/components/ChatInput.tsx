import { ChangeEvent, RefObject, useRef, useState } from 'react'

import { AppLanguage } from '~/services/language'
import Button from '~/components/button'
import TextArea from '~/components/textarea'
import { ImageAttachment } from '~/entities/messages'
import {
  AttachButton,
  ComposerError,
  ComposerRow,
  ImagePreview,
  ImagePreviewList,
  InputContainer
} from '../style'

const MAX_IMAGES = 4
const MAX_IMAGE_BYTES = 5 * 1024 * 1024
const ALLOWED_TYPES = new Set<ImageAttachment['media_type']>([
  'image/jpeg',
  'image/png',
  'image/webp'
])

function readFileAsDataUrl (file : File) : Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        resolve(reader.result)
      } else {
        reject(new Error(`Unable to read ${file.name}`))
      }
    }
    reader.onerror = () => reject(reader.error || new Error(`Unable to read ${file.name}`))
    reader.readAsDataURL(file)
  })
}

interface ChatInputProps {
  language : AppLanguage,
  textAreaRef : RefObject<HTMLTextAreaElement>,
  onSend : () => void,
  images : ImageAttachment[],
  onImagesSelected : (images : ImageAttachment[]) => void,
  onImageRemoved : (index : number) => void
}

export default function ChatInput ({
  language,
  textAreaRef,
  onSend,
  images,
  onImagesSelected,
  onImageRemoved
} : ChatInputProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [imageError, setImageError] = useState<string>()

  const handleFilesSelected = async (event : ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || [])
    event.target.value = ''
    if (files.length === 0) return
    if (images.length + files.length > MAX_IMAGES) {
      setImageError(`You can attach up to ${MAX_IMAGES} images.`)
      return
    }

    try {
      const attachments = await Promise.all(files.map(async (file) => {
        if (!ALLOWED_TYPES.has(file.type as ImageAttachment['media_type'])) {
          throw new Error(`${file.name}: use PNG, JPEG, or WebP.`)
        }
        if (file.size > MAX_IMAGE_BYTES) {
          throw new Error(`${file.name}: image must be 5 MB or smaller.`)
        }
        return {
          name: file.name,
          media_type: file.type as ImageAttachment['media_type'],
          data_url: await readFileAsDataUrl(file)
        }
      }))
      onImagesSelected(attachments)
      setImageError(undefined)
    } catch (error) {
      setImageError(error instanceof Error ? error.message : 'Unable to attach image.')
    }
  }

  return (
    <InputContainer>
      {images.length > 0 && (
        <ImagePreviewList>
          {images.map((image, index) => (
            <ImagePreview key={`${image.name}-${index}`}>
              <img alt={image.name} src={image.data_url} />
              <span title={image.name}>{image.name}</span>
              <button
                aria-label={`Remove ${image.name}`}
                onClick={() => onImageRemoved(index)}
                type='button'
              >
                x
              </button>
            </ImagePreview>
          ))}
        </ImagePreviewList>
      )}
      <ComposerRow>
        <input
          accept='image/png,image/jpeg,image/webp'
          hidden
          multiple
          onChange={handleFilesSelected}
          ref={fileInputRef}
          type='file'
        />
        <AttachButton
          aria-label={language === 'zh' ? '添加图片' : 'Attach images'}
          onClick={() => fileInputRef.current?.click()}
          title={language === 'zh' ? '添加图片' : 'Attach images'}
          type='button'
        >
          <svg aria-hidden='true' height='22' viewBox='0 0 24 24' width='22'>
            <path d='M16.5 6.5 8.9 14.1a3 3 0 1 0 4.2 4.2l7.1-7.1a5 5 0 0 0-7.1-7.1L5.7 11.5a7 7 0 1 0 9.9 9.9l5.2-5.2' fill='none' stroke='currentColor' strokeLinecap='round' strokeWidth='2' />
          </svg>
        </AttachButton>
        <TextArea
          placeholder={language === 'zh' ? '\u6709\u95ee\u9898\uff0c\u5c3d\u7ba1\u95ee' : 'ask any questions'}
          ref={textAreaRef}
        />
        <Button onClick={onSend}>
          <svg width='24' height='24' viewBox='0 0 14 16'><path fill='currentColor' d='m6.2 0.9q0.2-0.2 0.4-0.2 0.2-0.1 0.4-0.1 0.2 0 0.4 0.1 0.2 0 0.4 0.2l5.2 5.1c0.2 0.3 0.3 0.6 0.3 0.9 0 0.2-0.2 0.5-0.4 0.7-0.2 0.3-0.5 0.4-0.8 0.4-0.3 0-0.5-0.1-0.8-0.3l-3.2-3.2v9.8c0 0.3-0.1 0.6-0.3 0.8-0.2 0.2-0.5 0.3-0.8 0.3-0.3 0-0.6-0.1-0.8-0.3-0.2-0.2-0.3-0.5-0.3-0.8v-9.8l-3.2 3.2c-0.2 0.2-0.5 0.3-0.8 0.3-0.4 0-0.7-0.1-0.9-0.3-0.2-0.2-0.3-0.5-0.3-0.8 0-0.3 0.1-0.6 0.3-0.9z' /></svg>
        </Button>
      </ComposerRow>
      {imageError && <ComposerError role='alert'>{imageError}</ComposerError>}
    </InputContainer>
  )
}
