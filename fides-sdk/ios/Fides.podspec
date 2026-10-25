require 'json'

package = JSON.parse(File.read(File.join(__dir__, '..', 'package.json')))

Pod::Spec.new do |s|
  s.name           = 'Fides'
  s.version        = package['version']
  s.summary        = 'Trạng thái cuộc gọi CallKit, không đọc số điện thoại'
  s.homepage       = 'https://github.com/fides/fides-sdk'
  s.license        = 'MIT'
  s.author         = 'FIDES'
  s.platforms      = { :ios => '15.1' }
  s.swift_version  = '5.9'
  s.source         = { git: '' }
  s.static_framework = true
  s.frameworks     = 'CallKit'

  s.dependency 'ExpoModulesCore'

  s.pod_target_xcconfig = {
    'DEFINES_MODULE' => 'YES',
    'SWIFT_COMPILATION_MODE' => 'wholemodule'
  }

  s.source_files = "**/*.{h,m,swift}"
end
