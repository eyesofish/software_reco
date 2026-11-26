import { useEffect, useState } from 'react'
import { authenticatedFetch } from '~/services/auth'

export default function AuthenticatedImage ({ url, filename } : { url : string, filename : string }) {
  const [src, setSrc] = useState<string>()
  const [error, setError] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    let blobUrl : string|undefined
    setSrc(undefined); setError(false)
    authenticatedFetch(url, { signal: controller.signal }).then(async response => {
      if (!response.ok) throw new Error('Image unavailable')
      const blob = await response.blob()
      if (!active) return
      blobUrl = URL.createObjectURL(blob)
      setSrc(blobUrl)
    }).catch(() => { if (active && !controller.signal.aborted) setError(true) })
    return () => {
      active = false; controller.abort()
      if (blobUrl) URL.revokeObjectURL(blobUrl)
    }
  }, [url])
  if (error) return <span role='status'>Image unavailable: {filename}</span>
  return <a href={src} rel='noreferrer' target='_blank'><img alt={filename} src={src} /></a>
}
