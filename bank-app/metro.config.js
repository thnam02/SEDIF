const path = require("path")
const { getDefaultConfig } = require("expo/metro-config")

const config = getDefaultConfig(__dirname)
const sdk = path.resolve(__dirname, "../fides-sdk")
const docs = path.resolve(__dirname, "../docs")

const appModules = path.resolve(__dirname, "node_modules")

config.watchFolders = [...(config.watchFolders ?? []), sdk, docs]
config.resolver.unstable_enableSymlinks = true
config.resolver.nodeModulesPaths = [appModules]
config.resolver.extraNodeModules = { "fides-sdk": sdk }
config.resolver.blockList = [
  ...(Array.isArray(config.resolver.blockList) ? config.resolver.blockList : []),
  new RegExp(`${sdk.replace(/[/\\]/g, "[/\\\\]")}/node_modules/.*`),
]
const upstream = config.resolver.resolveRequest
config.resolver.resolveRequest = (context, moduleName, platform) => {
  const origin = context.originModulePath ?? ""
  const fromSdk = origin.startsWith(`${sdk}${path.sep}`) && !origin.includes(`${path.sep}node_modules${path.sep}`)
  const bare = !moduleName.startsWith(".") && !path.isAbsolute(moduleName)
  if (fromSdk && bare) {
    return (upstream ?? context.resolveRequest)(
      { ...context, nodeModulesPaths: [appModules], disableHierarchicalLookup: true },
      moduleName,
      platform,
    )
  }
  return (upstream ?? context.resolveRequest)(context, moduleName, platform)
}

module.exports = config
