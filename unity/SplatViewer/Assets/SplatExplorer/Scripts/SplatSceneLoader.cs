// Loads a reconstructed scene into a UnitySplats GsplatRenderer at runtime and puts the
// fly camera where the video started.
//
// How a scene is chosen (first match wins):
//   1. Deep link        splatexplorer://open?scene=<id>&server=<url>
//   2. Command line     -scene <id> [-server <url>]   or   -ply /path/to/file.ply
//   3. Inspector        sceneId / localPlyPath fields (handy in the Editor)
//   4. Otherwise a list of finished scenes is fetched from the server to pick from.
//
// Keys: H help, R reset view, L scene list, Esc release mouse.

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using Gsplat;
using UnityEngine;
using UnityEngine.Networking;

namespace SplatExplorer
{
    public class SplatSceneLoader : MonoBehaviour
    {
        [Header("References")]
        public GsplatRenderer splatRenderer;
        public FlyCamera flyCamera;

        [Header("Source")]
        [Tooltip("Splat Explorer backend started by scripts/start.sh")]
        public string serverUrl = "http://127.0.0.1:8000";
        [Tooltip("Optional: scene (job) id to load on start")]
        public string sceneId = "";
        [Tooltip("Optional: absolute path of a .ply/.spz/.sog to load directly, no server needed")]
        public string localPlyPath = "";

        [Header("Loading")]
        [Tooltip("Spark packs splats in GPU memory (smaller, recommended); Uncompressed is exact but large")]
        public CompressionMode compression = CompressionMode.Spark;
        [Tooltip("Brush/COLMAP output is RDF. Use RUB for PLYs made by most other 3DGS tools.")]
        public SourceCoordinates sourceCoordinates = SourceCoordinates.RDF;

        enum State { Idle, Picking, Downloading, Loading, Ready, Error }

        State m_state = State.Idle;
        string m_status = "";
        float m_progress;
        string m_error = "";
        bool m_showHelp = true;
        GsplatAsset m_asset;
        SceneMeta m_meta;
        Vector3 m_startPos;
        Vector3 m_startFwd = Vector3.forward;
        readonly List<SceneListEntry> m_scenes = new List<SceneListEntry>();
        Vector2 m_scroll;
        float m_fps;

        // ------------------------------------------------------------- startup --

        void Awake()
        {
            if (splatRenderer == null) splatRenderer = FindAnyObjectByType<GsplatRenderer>();
            if (flyCamera == null) flyCamera = FindAnyObjectByType<FlyCamera>();
        }

        void Start()
        {
            Application.deepLinkActivated += OnDeepLink;
            ParseCommandLine();
            if (!string.IsNullOrEmpty(Application.absoluteURL) && ApplyDeepLink(Application.absoluteURL))
            {
                // scene chosen by the link that launched the app
            }

            if (!string.IsNullOrWhiteSpace(localPlyPath))
                StartCoroutine(LoadLocal(localPlyPath.Trim()));
            else if (!string.IsNullOrWhiteSpace(sceneId))
                StartCoroutine(LoadFromServer(sceneId.Trim()));
            else
                OpenPicker();
        }

        void OnDestroy()
        {
            Application.deepLinkActivated -= OnDeepLink;
            ReleaseAsset();
        }

        void ParseCommandLine()
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                switch (args[i])
                {
                    case "-scene": sceneId = args[i + 1]; break;
                    case "-server": serverUrl = args[i + 1]; break;
                    case "-ply": localPlyPath = args[i + 1]; break;
                }
            }
        }

        void OnDeepLink(string url)
        {
            if (ApplyDeepLink(url) && !string.IsNullOrEmpty(sceneId))
            {
                StopAllCoroutines();
                StartCoroutine(LoadFromServer(sceneId));
            }
        }

        bool ApplyDeepLink(string url)
        {
            try
            {
                var uri = new Uri(url);
                if (!uri.Scheme.Equals("splatexplorer", StringComparison.OrdinalIgnoreCase))
                    return false;
                foreach (string part in uri.Query.TrimStart('?').Split('&'))
                {
                    int eq = part.IndexOf('=');
                    if (eq <= 0) continue;
                    string key = Uri.UnescapeDataString(part.Substring(0, eq));
                    string value = Uri.UnescapeDataString(part.Substring(eq + 1));
                    if (key == "scene") sceneId = value;
                    else if (key == "server") serverUrl = value;
                }
                localPlyPath = "";
                return true;
            }
            catch (UriFormatException)
            {
                return false;
            }
        }

        string Api(string path) => serverUrl.TrimEnd('/') + path;

        // ------------------------------------------------------------- picking --

        void OpenPicker()
        {
            m_state = State.Picking;
            if (flyCamera != null)
            {
                flyCamera.inputEnabled = false;
                flyCamera.ReleaseMouse();
            }
            StartCoroutine(FetchSceneList());
        }

        void ClosePicker()
        {
            if (flyCamera != null) flyCamera.inputEnabled = true;
        }

        IEnumerator FetchSceneList()
        {
            m_status = "Fetching scenes from " + serverUrl;
            using (var req = UnityWebRequest.Get(Api("/api/scenes")))
            {
                req.timeout = 10;
                yield return req.SendWebRequest();
                if (req.result != UnityWebRequest.Result.Success)
                {
                    m_status = $"Server not reachable at {serverUrl}. Is scripts/start.sh running? ({req.error})";
                    yield break;
                }
                m_scenes.Clear();
                var list = JsonUtility.FromJson<SceneListWrapper>("{\"items\":" + req.downloadHandler.text + "}");
                if (list?.items != null) m_scenes.AddRange(list.items);
                m_status = m_scenes.Count == 0
                    ? "No finished scenes yet. Upload a video in the web app first."
                    : $"{m_scenes.Count} scene(s) available";
            }
        }

        // ------------------------------------------------------------- loading --

        IEnumerator LoadFromServer(string id)
        {
            ClosePicker();
            m_state = State.Downloading;
            m_progress = 0f;
            m_status = "Fetching scene info";
            sceneId = id;

            SceneMeta meta;
            using (var req = UnityWebRequest.Get(Api($"/api/scenes/{id}/scene.json")))
            {
                req.timeout = 20;
                yield return req.SendWebRequest();
                if (req.result != UnityWebRequest.Result.Success)
                {
                    Fail($"Could not get scene {id} from {serverUrl}: {req.error}");
                    yield break;
                }
                meta = JsonUtility.FromJson<SceneMeta>(req.downloadHandler.text);
            }

            string dir = Path.Combine(Application.temporaryCachePath, "scenes", id);
            Directory.CreateDirectory(dir);
            string plyPath = Path.Combine(dir, "scene.ply");
            bool cached = File.Exists(plyPath) && meta.ply_size_bytes > 0 &&
                          new FileInfo(plyPath).Length == meta.ply_size_bytes;
            if (!cached)
            {
                string tmp = plyPath + ".part";
                using (var req = new UnityWebRequest(Api($"/api/scenes/{id}/scene.ply"), UnityWebRequest.kHttpVerbGET))
                {
                    req.downloadHandler = new DownloadHandlerFile(tmp) { removeFileOnAbort = true };
                    var op = req.SendWebRequest();
                    while (!op.isDone)
                    {
                        m_progress = req.downloadProgress;
                        m_status = $"Downloading {meta.name} ({FormatBytes(meta.ply_size_bytes)})";
                        yield return null;
                    }
                    if (req.result != UnityWebRequest.Result.Success)
                    {
                        Fail($"Download failed: {req.error}");
                        yield break;
                    }
                }
                if (File.Exists(plyPath)) File.Delete(plyPath);
                File.Move(tmp, plyPath);
            }
            yield return LoadPly(plyPath, meta);
        }

        IEnumerator LoadLocal(string path)
        {
            ClosePicker();
            if (!File.Exists(path))
            {
                Fail($"File not found: {path}");
                yield break;
            }
            // Use scene.json next to the file when present (pipeline output folder).
            SceneMeta meta = null;
            string json = Path.Combine(Path.GetDirectoryName(path) ?? "", "scene.json");
            if (File.Exists(json))
                meta = JsonUtility.FromJson<SceneMeta>(File.ReadAllText(json));
            yield return LoadPly(path, meta);
        }

        IEnumerator LoadPly(string path, SceneMeta meta)
        {
            m_state = State.Loading;
            m_progress = 0f;
            m_status = "Decoding splats (the window may pause for a few seconds)";
            yield return null; // let the status draw before the blocking decode
            yield return null;

            GsplatAsset asset;
            try
            {
                asset = GsplatRuntimeLoader.LoadFile(path, compression, sourceCoordinates);
            }
            catch (Exception e)
            {
                Fail($"Could not load {Path.GetFileName(path)}: {e.Message}");
                yield break;
            }

            ReleaseAsset();
            m_asset = asset;
            m_meta = meta ?? new SceneMeta { name = Path.GetFileNameWithoutExtension(path) };
            if (m_meta.splat_count == 0) m_meta.splat_count = (int)asset.SplatCount;
            splatRenderer.GsplatAsset = asset;
            ApplyAlignment(m_meta);
            m_state = State.Ready;
            m_status = "";
        }

        /// <summary>
        /// COLMAP's world has no notion of gravity. Rotate the splat so the average camera "up"
        /// (people hold phones upright) becomes Unity's up, then start where the video started.
        /// </summary>
        void ApplyAlignment(SceneMeta meta)
        {
            Transform root = splatRenderer.transform;
            var cam = flyCamera != null ? flyCamera.GetComponent<Camera>() : Camera.main;

            if (meta == null || !meta.HasCameras)
            {
                root.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
                Bounds b = splatRenderer.Bounds;
                m_startPos = b.center - Vector3.forward * Mathf.Max(1f, b.extents.magnitude);
                m_startFwd = Vector3.forward;
                if (flyCamera != null) flyCamera.baseSpeed = Mathf.Clamp(b.extents.magnitude * 0.2f, 0.05f, 50f);
            }
            else
            {
                Vector3 avgUp = SceneMeta.ToUnity(meta.cameras.average_up).normalized;
                Quaternion align = avgUp.sqrMagnitude > 0.5f
                    ? Quaternion.FromToRotation(avgUp, Vector3.up)
                    : Quaternion.identity;
                root.SetPositionAndRotation(Vector3.zero, align);

                m_startPos = align * SceneMeta.ToUnity(meta.cameras.start.position);
                m_startFwd = align * SceneMeta.ToUnity(meta.cameras.start.forward);
                float extent = Mathf.Max(0.01f, meta.cameras.camera_path_extent);
                if (flyCamera != null) flyCamera.baseSpeed = Mathf.Clamp(extent / 4f, 0.02f, 50f);
                if (cam != null)
                {
                    cam.nearClipPlane = Mathf.Clamp(extent * 0.002f, 0.001f, 0.1f);
                    cam.farClipPlane = Mathf.Max(100f, extent * 200f);
                }
            }
            ResetView();
        }

        void ResetView()
        {
            if (flyCamera != null) flyCamera.SetPose(m_startPos, m_startFwd);
        }

        void ReleaseAsset()
        {
            if (splatRenderer != null && splatRenderer.GsplatAsset == m_asset)
                splatRenderer.GsplatAsset = null;
            if (m_asset != null) Destroy(m_asset);
            m_asset = null;
        }

        void Fail(string message)
        {
            m_state = State.Error;
            m_error = message;
            Debug.LogError("[SplatExplorer] " + message);
            if (flyCamera != null) flyCamera.ReleaseMouse();
        }

        // ----------------------------------------------------------------- UI --

        void Update()
        {
            m_fps = Mathf.Lerp(m_fps, 1f / Mathf.Max(1e-4f, Time.unscaledDeltaTime), 0.05f);
            if (m_state == State.Ready)
            {
                if (KeyDown(KeyCode.H)) m_showHelp = !m_showHelp;
                if (KeyDown(KeyCode.R)) ResetView();
                if (KeyDown(KeyCode.L)) OpenPicker();
            }
        }

        static bool KeyDown(KeyCode key)
        {
#if ENABLE_INPUT_SYSTEM
            var k = UnityEngine.InputSystem.Keyboard.current;
            if (k == null) return false;
            switch (key)
            {
                case KeyCode.H: return k.hKey.wasPressedThisFrame;
                case KeyCode.R: return k.rKey.wasPressedThisFrame;
                case KeyCode.L: return k.lKey.wasPressedThisFrame;
            }
            return false;
#else
            return Input.GetKeyDown(key);
#endif
        }

        void OnGUI()
        {
            GUI.skin.label.fontSize = 14;
            GUI.skin.button.fontSize = 14;
            GUI.skin.textField.fontSize = 14;
            switch (m_state)
            {
                case State.Picking: DrawPicker(); break;
                case State.Downloading:
                case State.Loading: DrawBox(m_status, m_state == State.Downloading ? m_progress : -1f); break;
                case State.Error: DrawError(); break;
                case State.Ready: DrawHud(); break;
            }
        }

        void DrawBox(string text, float progress)
        {
            var rect = new Rect(Screen.width / 2f - 260, Screen.height / 2f - 40, 520, 80);
            GUI.Box(rect, GUIContent.none);
            GUI.Label(new Rect(rect.x + 16, rect.y + 12, rect.width - 32, 24), text);
            if (progress >= 0f)
            {
                var bar = new Rect(rect.x + 16, rect.y + 46, rect.width - 32, 12);
                GUI.Box(bar, GUIContent.none);
                GUI.Box(new Rect(bar.x, bar.y, bar.width * Mathf.Clamp01(progress), bar.height), GUIContent.none);
            }
        }

        void DrawError()
        {
            var rect = new Rect(Screen.width / 2f - 300, Screen.height / 2f - 70, 600, 140);
            GUI.Box(rect, "Something went wrong");
            GUI.Label(new Rect(rect.x + 16, rect.y + 28, rect.width - 32, 70), m_error);
            if (GUI.Button(new Rect(rect.x + 16, rect.y + 100, 160, 28), "Scene list")) OpenPicker();
        }

        void DrawPicker()
        {
            var rect = new Rect(Screen.width / 2f - 320, 60, 640, Mathf.Min(Screen.height - 120, 560));
            GUILayout.BeginArea(rect, "Splat Explorer - choose a scene", GUI.skin.window);
            GUILayout.Space(6);
            GUILayout.BeginHorizontal();
            GUILayout.Label("Server", GUILayout.Width(60));
            serverUrl = GUILayout.TextField(serverUrl);
            if (GUILayout.Button("Refresh", GUILayout.Width(90))) StartCoroutine(FetchSceneList());
            GUILayout.EndHorizontal();
            GUILayout.Label(m_status);

            m_scroll = GUILayout.BeginScrollView(m_scroll);
            foreach (var s in m_scenes)
            {
                GUILayout.BeginHorizontal(GUI.skin.box);
                GUILayout.Label($"{s.name}\n{s.splat_count:N0} splats - {FormatBytes(s.ply_size_bytes)}");
                if (GUILayout.Button("Open", GUILayout.Width(90), GUILayout.Height(40)))
                    StartCoroutine(LoadFromServer(s.id));
                GUILayout.EndHorizontal();
            }
            GUILayout.EndScrollView();

            GUILayout.Space(8);
            GUILayout.Label("Or open a local .ply file:");
            GUILayout.BeginHorizontal();
            localPlyPath = GUILayout.TextField(localPlyPath);
            if (GUILayout.Button("Open file", GUILayout.Width(90)) && !string.IsNullOrWhiteSpace(localPlyPath))
                StartCoroutine(LoadLocal(localPlyPath.Trim()));
            GUILayout.EndHorizontal();
            if (m_asset != null && GUILayout.Button("Back to current scene"))
            {
                m_state = State.Ready;
                ClosePicker();
            }
            GUILayout.EndArea();
        }

        void DrawHud()
        {
            string title = $"{m_meta?.name}   {m_meta?.splat_count:N0} splats   {m_fps:0} fps";
            if (!m_showHelp)
            {
                GUI.Label(new Rect(12, 8, 700, 24), title + "   (H for help)");
                return;
            }
            const string help =
                "W A S D  move        Mouse  look (click to capture, Esc to release)\n" +
                "Space  up            Ctrl / C / Q  down        Shift  faster\n" +
                "Scroll  change speed     R  reset view     L  scene list     H  hide help";
            GUI.Box(new Rect(8, 8, 620, 94), GUIContent.none);
            GUI.Label(new Rect(18, 12, 600, 24), title);
            GUI.Label(new Rect(18, 36, 600, 66), help);
        }

        static string FormatBytes(long bytes)
        {
            if (bytes <= 0) return "?";
            if (bytes > 1L << 30) return $"{bytes / (double)(1L << 30):0.0} GB";
            return $"{bytes / (double)(1L << 20):0} MB";
        }
    }
}
