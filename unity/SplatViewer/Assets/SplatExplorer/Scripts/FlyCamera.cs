// Free-fly camera for exploring a Gaussian splat scene. No physics, no collisions.
//
//   W / S        forward / back           A / D   left / right
//   Mouse        look (click the view to capture the mouse, Esc to release)
//   Space        up                       Ctrl (or C / Q)  down
//   Shift (hold) faster                   Scroll  change base speed
//
// Works with either input backend: the Input System package or the legacy Input Manager.

using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace SplatExplorer
{
    [RequireComponent(typeof(Camera))]
    public class FlyCamera : MonoBehaviour
    {
        [Tooltip("Base movement speed in scene units per second. Set automatically from the scene size.")]
        public float baseSpeed = 1.5f;
        [Tooltip("Speed multiplier while Shift is held.")]
        public float boostMultiplier = 4f;
        [Tooltip("Degrees of rotation per pixel of mouse movement.")]
        public float lookSensitivity = 0.12f;
        public bool invertY;
        [Tooltip("Higher = snappier movement, lower = smoother glide.")]
        public float acceleration = 10f;
        [Tooltip("When false the camera ignores input (used while a menu is open).")]
        public bool inputEnabled = true;

        float m_yaw;
        float m_pitch;
        Vector3 m_velocity;

        public bool MouseCaptured => Cursor.lockState == CursorLockMode.Locked;

        void Start() => SyncAnglesFromTransform();

        /// <summary>Place the camera; roll is dropped so the horizon stays level.</summary>
        public void SetPose(Vector3 position, Vector3 forward)
        {
            transform.position = position;
            if (forward.sqrMagnitude > 1e-8f)
                transform.rotation = Quaternion.LookRotation(forward.normalized, Vector3.up);
            m_velocity = Vector3.zero;
            SyncAnglesFromTransform();
        }

        public void SyncAnglesFromTransform()
        {
            Vector3 e = transform.eulerAngles;
            m_yaw = e.y;
            m_pitch = e.x > 180f ? e.x - 360f : e.x;
        }

        public void ReleaseMouse()
        {
            Cursor.lockState = CursorLockMode.None;
            Cursor.visible = true;
        }

        void Update()
        {
            float dt = Time.unscaledDeltaTime;
            if (!inputEnabled)
            {
                m_velocity = Vector3.zero;
                return;
            }

            // Mouse capture --------------------------------------------------
            if (Input_EscapePressed())
                ReleaseMouse();
            else if (!MouseCaptured && Input_ClickPressed() && GUIUtility.hotControl == 0)
            {
                Cursor.lockState = CursorLockMode.Locked;
                Cursor.visible = false;
            }

            // Look ------------------------------------------------------------
            if (MouseCaptured)
            {
                Vector2 delta = Input_MouseDelta();
                m_yaw += delta.x * lookSensitivity;
                m_pitch += (invertY ? 1f : -1f) * delta.y * lookSensitivity;
                m_pitch = Mathf.Clamp(m_pitch, -89f, 89f);
                transform.rotation = Quaternion.Euler(m_pitch, m_yaw, 0f);
            }

            // Speed adjust with scroll wheel ---------------------------------
            float scroll = Input_Scroll();
            if (Mathf.Abs(scroll) > 0.01f)
                baseSpeed = Mathf.Clamp(baseSpeed * Mathf.Pow(1.15f, Mathf.Sign(scroll)), 0.01f, 500f);

            // Move ------------------------------------------------------------
            Vector3 local = Vector3.zero;
            if (Input_Held(Act.Forward)) local += Vector3.forward;
            if (Input_Held(Act.Back)) local += Vector3.back;
            if (Input_Held(Act.Left)) local += Vector3.left;
            if (Input_Held(Act.Right)) local += Vector3.right;
            Vector3 move = transform.rotation * local;
            if (Input_Held(Act.Up)) move += Vector3.up;      // world up
            if (Input_Held(Act.Down)) move += Vector3.down;
            if (move.sqrMagnitude > 1f) move.Normalize();

            float speed = baseSpeed * (Input_Held(Act.Boost) ? boostMultiplier : 1f);
            Vector3 target = move * speed;
            m_velocity = Vector3.Lerp(m_velocity, target, 1f - Mathf.Exp(-acceleration * dt));
            transform.position += m_velocity * dt;
        }

        // --------------------------------------------------------------- input --

        enum Act { Forward, Back, Left, Right, Up, Down, Boost }

#if ENABLE_INPUT_SYSTEM
        static bool Input_Held(Act a)
        {
            var k = Keyboard.current;
            if (k == null) return false;
            switch (a)
            {
                case Act.Forward: return k.wKey.isPressed || k.upArrowKey.isPressed;
                case Act.Back: return k.sKey.isPressed || k.downArrowKey.isPressed;
                case Act.Left: return k.aKey.isPressed || k.leftArrowKey.isPressed;
                case Act.Right: return k.dKey.isPressed || k.rightArrowKey.isPressed;
                case Act.Up: return k.spaceKey.isPressed || k.eKey.isPressed;
                case Act.Down: return k.leftCtrlKey.isPressed || k.rightCtrlKey.isPressed || k.cKey.isPressed || k.qKey.isPressed;
                case Act.Boost: return k.leftShiftKey.isPressed || k.rightShiftKey.isPressed;
            }
            return false;
        }

        static Vector2 Input_MouseDelta() => Mouse.current != null ? Mouse.current.delta.ReadValue() : Vector2.zero;
        static float Input_Scroll() => Mouse.current != null ? Mouse.current.scroll.ReadValue().y / 120f : 0f;
        static bool Input_ClickPressed() => Mouse.current != null && Mouse.current.leftButton.wasPressedThisFrame;
        static bool Input_EscapePressed() => Keyboard.current != null && Keyboard.current.escapeKey.wasPressedThisFrame;
#else
        static bool Input_Held(Act a)
        {
            switch (a)
            {
                case Act.Forward: return Input.GetKey(KeyCode.W) || Input.GetKey(KeyCode.UpArrow);
                case Act.Back: return Input.GetKey(KeyCode.S) || Input.GetKey(KeyCode.DownArrow);
                case Act.Left: return Input.GetKey(KeyCode.A) || Input.GetKey(KeyCode.LeftArrow);
                case Act.Right: return Input.GetKey(KeyCode.D) || Input.GetKey(KeyCode.RightArrow);
                case Act.Up: return Input.GetKey(KeyCode.Space) || Input.GetKey(KeyCode.E);
                case Act.Down: return Input.GetKey(KeyCode.LeftControl) || Input.GetKey(KeyCode.RightControl) || Input.GetKey(KeyCode.C) || Input.GetKey(KeyCode.Q);
                case Act.Boost: return Input.GetKey(KeyCode.LeftShift) || Input.GetKey(KeyCode.RightShift);
            }
            return false;
        }

        // Legacy "Mouse X/Y" axes are pixel deltas scaled by the default sensitivity of 0.1.
        static Vector2 Input_MouseDelta() => new Vector2(Input.GetAxisRaw("Mouse X"), Input.GetAxisRaw("Mouse Y")) / 0.1f;
        static float Input_Scroll() => Input.mouseScrollDelta.y;
        static bool Input_ClickPressed() => Input.GetMouseButtonDown(0);
        static bool Input_EscapePressed() => Input.GetKeyDown(KeyCode.Escape);
#endif
    }
}
