import { Suspense, useEffect, useState } from 'react'

import SplashScreen from '~/components/SplashScreen'
import AppBoot from '~/services/appBoot'

interface SplashScreenProps {
  children ?: JSX.Element
}

function SplashLifecycle ({ children } : SplashScreenProps) {

  const [showSplash, setShowSplash] = useState(true)

  useEffect(() => {
    if (!children) {
      setShowSplash(false)
      return
    }

    let active = true
    const minSplashDelay = new Promise(resolve => setTimeout(resolve, 3000))

    Promise.all([AppBoot.run(), minSplashDelay]).finally(() => {
      if (active) setShowSplash(false)
    })

    return () => {
      active = false
    }
  }, [])

  return (
    <>
      { showSplash && <SplashScreen /> }
      { children }
    </>
  )

}

export default function LoadingWrapper ({ children } : SplashScreenProps) {

  return (
    <Suspense fallback={<SplashScreen />}>
      <SplashLifecycle>{ children }</SplashLifecycle>
    </Suspense>
  )

}
