const { withPodfile } = require("@expo/config-plugins");

const MARKER = "fides_pod_deployment_target";

const SNIPPET = [
  `    # ${MARKER}`,
  "    installer.pods_project.targets.each do |target|",
  "      target.build_configurations.each do |config|",
  "        current = config.build_settings['IPHONEOS_DEPLOYMENT_TARGET']",
  "        next if current.nil? || current.to_s !~ /\\A\\d+(\\.\\d+)*\\z/",
  "        if Gem::Version.new(current.to_s) < Gem::Version.new('15.1')",
  "          config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = '15.1'",
  "        end",
  "      end",
  "    end",
].join("\n");

/** Ép mọi pod còn deployment target dưới 15.1 lên 15.1 sau react_native_post_install. */
function withPodDeploymentTarget(config) {
  return withPodfile(config, (podConfig) => {
    const file = podConfig.modResults;
    const contents = file.contents;
    if (contents.includes(MARKER)) return podConfig;
    const start = contents.indexOf("post_install do |installer|");
    if (start < 0) throw new Error("Podfile không có post_install");
    const close = contents.indexOf("\n  end", start);
    if (close < 0) throw new Error("Podfile không đóng post_install");
    file.contents = `${contents.slice(0, close)}\n${SNIPPET}${contents.slice(close)}`;
    return podConfig;
  });
}

module.exports = withPodDeploymentTarget;
