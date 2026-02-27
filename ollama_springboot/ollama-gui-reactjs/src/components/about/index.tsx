import { ColumnContainer } from '~/components/containers'
import { I18N, useAppLanguage } from '~/services/language'
import './index.css'

export default function About () {
  const language = useAppLanguage()

  return (
    <ColumnContainer style={{ alignItems: 'center', padding: '8px', textAlign: 'center', width: '100%' }}>
      <p className='landingTitle'>{I18N[language].aboutDescription}</p>
    </ColumnContainer>
  )

}
