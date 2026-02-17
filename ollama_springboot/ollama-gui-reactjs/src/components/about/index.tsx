import { ColumnContainer } from '~/components/containers'
import './index.css'

export default function About () {

  return (
    <ColumnContainer style={{ alignItems: 'flex-start', padding: '8px' }}>
      <p className='landingTitle'>This is a software recommendation system based on large language models.</p>
    </ColumnContainer>
  )

}
