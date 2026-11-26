import Store from '~/services/store'
import { disConfig } from '~/stores/config'
import { updateConfig } from '~/stores/config/actions'
import { sanitizeConfig } from '~/config/defaults'

export default class AppBoot {

  public static async run () {
    AppBoot.updateConfigHandler()

    await AppBoot.sleep(1000)
  }

  private static sleep (ms : number) {
    return new Promise(resolve => setTimeout(resolve, ms))
  }

  private static updateConfigHandler () {
    const rawConfig = Store.get('config')
    const { config, changed } = sanitizeConfig(rawConfig)
    disConfig(updateConfig(config))
    if (changed) {
      Store.set('config', config)
    }
  }

}
