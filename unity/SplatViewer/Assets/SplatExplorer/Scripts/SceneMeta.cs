// Mirror of scene.json written by the reconstruction pipeline (splat_explorer/pipeline/export.py).
// All vectors are in COLMAP world coordinates: x right, y down, z forward (RDF).

using System;
using UnityEngine;

namespace SplatExplorer
{
    [Serializable]
    public class ScenePose
    {
        public float[] position;
        public float[] forward;
        public float[] up;
    }

    [Serializable]
    public class SceneCameras
    {
        public int camera_count;
        public ScenePose start;
        public float[] average_up;
        public float[] camera_bounds_min;
        public float[] camera_bounds_max;
        public float camera_path_extent;
        public float[] camera_path_flat;
    }

    [Serializable]
    public class SceneMeta
    {
        public string id;
        public string name;
        public string quality;
        public int splat_count;
        public long ply_size_bytes;
        public SceneCameras cameras;

        public bool HasCameras =>
            cameras != null && cameras.camera_count > 0 && cameras.start != null &&
            IsVec(cameras.start.position) && IsVec(cameras.start.forward) && IsVec(cameras.average_up);

        static bool IsVec(float[] v) => v != null && v.Length == 3;

        /// <summary>COLMAP (RDF) -> Unity (RUF): flip Y. Matches SourceCoordinates.RDF in UnitySplats.</summary>
        public static Vector3 ToUnity(float[] v) =>
            v != null && v.Length == 3 ? new Vector3(v[0], -v[1], v[2]) : Vector3.zero;
    }

    [Serializable]
    public class SceneListEntry
    {
        public string id;
        public string name;
        public int splat_count;
        public long ply_size_bytes;
        public double created_at;
    }

    [Serializable]
    class SceneListWrapper
    {
        public SceneListEntry[] items;
    }
}
