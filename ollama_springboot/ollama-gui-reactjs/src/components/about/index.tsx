import { ColumnContainer } from '~/components/containers'
import { I18N, useAppLanguage } from '~/services/language'
import './index.css'

export default function About () {
  const language = useAppLanguage()

  return (
    <ColumnContainer style={{ alignItems: 'flex-start', padding: '8px' }}>
      <p className='landingTitle'>{I18N[language].aboutDescription}</p>
    </ColumnContainer>
  )

}
