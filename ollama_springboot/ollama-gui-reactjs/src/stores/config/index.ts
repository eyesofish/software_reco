import { createStore } from 'react-hooks-global-state'

import reducer from './reducer'
import { getDefaultConfig } from '~/config/defaults'

export const STATE = {
  config: getDefaultConfig()
}

export const {
  dispatch: disConfig,
  useStoreState: useConfig
} = createStore(reducer, STATE)
