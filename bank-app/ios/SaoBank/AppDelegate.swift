import EXDevLauncher
import Expo
import React
import ReactAppDependencyProvider

@UIApplicationMain
public class AppDelegate: ExpoAppDelegate {
  var window: UIWindow?

  var reactNativeDelegate: ExpoReactNativeFactoryDelegate?
  var reactNativeFactory: RCTReactNativeFactory?
  var launchOptions: [UIApplication.LaunchOptionsKey: Any]?
  private var reactStarted = false

  func startReactIfNeeded() {
    guard !reactStarted, let window, let factory = reactNativeFactory else { return }
    reactStarted = true
    factory.startReactNative(
      withModuleName: "main",
      in: window,
      launchOptions: launchOptions)
  }

  public override func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    let delegate = ReactNativeDelegate()
    let factory = ExpoReactNativeFactory(delegate: delegate)
    delegate.dependencyProvider = RCTAppDependencyProvider()

    reactNativeDelegate = delegate
    reactNativeFactory = factory
    bindReactNativeFactory(factory)
    self.launchOptions = launchOptions

#if os(iOS) || os(tvOS)
    // The dev launcher reads the key window inside super.application, which runs
    // before the scene exists. This placeholder keeps that assertion from firing.
    // scene(_:willConnectTo:) moves the interface onto the scene window.
    let placeholder = UIWindow(frame: UIScreen.main.bounds)
    window = placeholder
    placeholder.makeKeyAndVisible()
    startReactIfNeeded()
#endif

    return super.application(application, didFinishLaunchingWithOptions: launchOptions)
  }

  // iOS 26+ aborts an app that still creates its window from the app delegate.
  public func application(
    _ application: UIApplication,
    configurationForConnecting connectingSceneSession: UISceneSession,
    options: UIScene.ConnectionOptions
  ) -> UISceneConfiguration {
    let configuration = UISceneConfiguration(
      name: "Default Configuration",
      sessionRole: connectingSceneSession.role)
    configuration.delegateClass = SceneDelegate.self
    return configuration
  }

  // Linking API
  public override func application(
    _ app: UIApplication,
    open url: URL,
    options: [UIApplication.OpenURLOptionsKey: Any] = [:]
  ) -> Bool {
    return super.application(app, open: url, options: options) || RCTLinkingManager.application(app, open: url, options: options)
  }

  // Universal Links
  public override func application(
    _ application: UIApplication,
    continue userActivity: NSUserActivity,
    restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void
  ) -> Bool {
    let result = RCTLinkingManager.application(application, continue: userActivity, restorationHandler: restorationHandler)
    return super.application(application, continue: userActivity, restorationHandler: restorationHandler) || result
  }
}

public class SceneDelegate: UIResponder, UIWindowSceneDelegate {
  public var window: UIWindow?

  public func scene(
    _ scene: UIScene,
    willConnectTo session: UISceneSession,
    options connectionOptions: UIScene.ConnectionOptions
  ) {
    guard let windowScene = scene as? UIWindowScene,
      let appDelegate = UIApplication.shared.delegate as? AppDelegate
    else {
      return
    }
    let window = UIWindow(windowScene: windowScene)
    self.window = window
    if let existing = appDelegate.window, existing !== window {
      window.rootViewController = existing.rootViewController
      existing.isHidden = true
    }
    appDelegate.window = window
    if let url = connectionOptions.urlContexts.first?.url {
      var launchOptions = appDelegate.launchOptions ?? [:]
      launchOptions[.url] = url
      appDelegate.launchOptions = launchOptions
      _ = RCTLinkingManager.application(UIApplication.shared, open: url, options: [:])
    }
    window.makeKeyAndVisible()
    EXDevLauncherController.sharedInstance().setValue(window, forKey: "window")
    appDelegate.startReactIfNeeded()
  }

  public func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
    guard let url = URLContexts.first?.url else { return }
    _ = RCTLinkingManager.application(UIApplication.shared, open: url, options: [:])
  }
}

class ReactNativeDelegate: ExpoReactNativeFactoryDelegate {
  // Extension point for config-plugins

  override func sourceURL(for bridge: RCTBridge) -> URL? {
    // needed to return the correct URL for expo-dev-client.
    bridge.bundleURL ?? bundleURL()
  }

  override func bundleURL() -> URL? {
#if DEBUG
    return RCTBundleURLProvider.sharedSettings().jsBundleURL(forBundleRoot: ".expo/.virtual-metro-entry")
#else
    return Bundle.main.url(forResource: "main", withExtension: "jsbundle")
#endif
  }
}
