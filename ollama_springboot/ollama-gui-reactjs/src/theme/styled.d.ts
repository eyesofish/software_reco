import 'styled-components'
import { AppTheme } from './tokens'

declare module 'styled-components' {
  export interface DefaultTheme extends AppTheme {}
}
