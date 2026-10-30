// Editor menu: "Splat Explorer" in the Unity menu bar.
//   Create Viewer Scene   builds Assets/Scenes/SplatViewer.unity and sets Player Settings
//   Build macOS Viewer    builds Builds/SplatViewer.app (Apple Silicon, Metal)

using System.IO;
using Gsplat;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

namespace SplatExplorer.EditorTools
{
    public static class SplatExplorerMenu
    {
        const string ScenePath = "Assets/Scenes/SplatViewer.unity";
        const string UrlScheme = "splatexplorer";

        [MenuItem("Splat Explorer/1. Create Viewer Scene", priority = 1)]
        public static void CreateViewerScene()
        {
            if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo())
                return;

            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

            var camGo = new GameObject("Main Camera") { tag = "MainCamera" };
            var cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.05f, 0.05f, 0.06f);
            cam.nearClipPlane = 0.01f;
            cam.farClipPlane = 1000f;
            cam.fieldOfView = 70f;
            cam.allowMSAA = false;
            var fly = camGo.AddComponent<FlyCamera>();

            var splatGo = new GameObject("Splat");
            var splat = splatGo.AddComponent<GsplatRenderer>();

            var loaderGo = new GameObject("SplatViewer");
            var loader = loaderGo.AddComponent<SplatSceneLoader>();
            loader.splatRenderer = splat;
            loader.flyCamera = fly;

            Directory.CreateDirectory("Assets/Scenes");
            EditorSceneManager.SaveScene(scene, ScenePath);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(ScenePath, true) };

            ConfigurePlayer();
            AssetDatabase.SaveAssets();
            Debug.Log("[SplatExplorer] Created " + ScenePath + ". Press Play to pick a scene from the server.");
        }

        [MenuItem("Splat Explorer/2. Build macOS Viewer", priority = 2)]
        public static void BuildMacViewer()
        {
            if (!File.Exists(ScenePath))
                CreateViewerScene();
            ConfigurePlayer();

            Directory.CreateDirectory("Builds");
            var options = new BuildPlayerOptions
            {
                scenes = new[] { ScenePath },
                locationPathName = "Builds/SplatViewer.app",
                target = BuildTarget.StandaloneOSX,
                options = BuildOptions.None,
            };
            BuildReport report = BuildPipeline.BuildPlayer(options);
            if (report.summary.result == BuildResult.Succeeded)
            {
                Debug.Log($"[SplatExplorer] Built {options.locationPathName} ({report.summary.totalSize / (1024 * 1024)} MB). " +
                          "Copy it to /Applications to make the web app's 'Open in SplatViewer' link work.");
                EditorUtility.RevealInFinder(options.locationPathName);
            }
            else
            {
                Debug.LogError($"[SplatExplorer] Build {report.summary.result}. See the Console for details.");
            }
        }

        static void ConfigurePlayer()
        {
            PlayerSettings.productName = "SplatViewer";
            PlayerSettings.runInBackground = true;
            PlayerSettings.colorSpace = ColorSpace.Linear;
            PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
            PlayerSettings.defaultScreenWidth = 1600;
            PlayerSettings.defaultScreenHeight = 900;
            PlayerSettings.resizableWindow = true;
            PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.StandaloneOSX, false);
            PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneOSX, new[] { GraphicsDeviceType.Metal });
            // The backend runs on http://127.0.0.1, which Unity blocks unless HTTP is allowed.
            PlayerSettings.insecureHttpOption = InsecureHttpOption.AlwaysAllowed;
            TrySetMacUrlScheme();
        }

        /// Registers splatexplorer:// so the web app's "Open in SplatViewer" link launches the build.
        /// Done through the serialized ProjectSettings because there is no stable public API for it.
        static void TrySetMacUrlScheme()
        {
            var assets = AssetDatabase.LoadAllAssetsAtPath("ProjectSettings/ProjectSettings.asset");
            if (assets == null || assets.Length == 0) return;
            var so = new SerializedObject(assets[0]);
            var prop = so.FindProperty("macOSURLSchemes");
            if (prop == null || !prop.isArray)
            {
                Debug.Log("[SplatExplorer] Set the URL scheme manually: Player Settings > Other Settings > " +
                          "Mac Configuration > Supported URL schemes > add 'splatexplorer'.");
                return;
            }
            for (int i = 0; i < prop.arraySize; i++)
                if (prop.GetArrayElementAtIndex(i).stringValue == UrlScheme)
                    return;
            prop.InsertArrayElementAtIndex(prop.arraySize);
            prop.GetArrayElementAtIndex(prop.arraySize - 1).stringValue = UrlScheme;
            so.ApplyModifiedPropertiesWithoutUndo();
        }
    }
}
