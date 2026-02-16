import { ColumnContainer } from '~/components/containers'
import './index.css'

export default function About () {

  return (
    <ColumnContainer style={{ alignItems: 'flex-start', padding: '8px' }}>
      <p className='landingTitle'>这是一个基于大语言模型的软件推荐系统。</p>
    </ColumnContainer>
  )

}
