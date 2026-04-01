(function () {
  const BADGE_ID = "chainlit-role-badge";

  function getRoleLabel(user) {
    const metadata = (user && user.metadata) || {};
    return metadata.is_staff || metadata.is_superuser ? "Admin" : "User";
  }

  function ensureBadge() {
    let badge = document.getElementById(BADGE_ID);
    if (!badge) {
      badge = document.createElement("div");
      badge.id = BADGE_ID;
      badge.className = "chainlit-role-badge";
      badge.innerHTML =
        '<span class="chainlit-role-badge__label">Role:</span><span class="chainlit-role-badge__value"></span>';
      document.body.appendChild(badge);
    }
    return badge;
  }

  function removeBadge() {
    const badge = document.getElementById(BADGE_ID);
    if (badge) {
      badge.remove();
    }
  }

  async function refreshRoleBadge() {
    try {
      const response = await fetch("/user", {
        credentials: "include",
        headers: {
          Accept: "application/json",
        },
      });

      if (!response.ok) {
        removeBadge();
        return;
      }

      const user = await response.json();
      if (!user || !user.identifier) {
        removeBadge();
        return;
      }

      const badge = ensureBadge();
      const roleLabel = getRoleLabel(user);
      const roleValue = badge.querySelector(".chainlit-role-badge__value");

      if (roleValue) {
        roleValue.textContent = roleLabel;
      }

      const displayName = user.display_name || user.identifier;
      badge.title = displayName ? displayName + " (" + roleLabel + ")" : roleLabel;
      badge.dataset.role = roleLabel.toLowerCase();
    } catch (error) {
      removeBadge();
    }
  }

  function boot() {
    refreshRoleBadge();
    window.setInterval(refreshRoleBadge, 15000);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) {
        refreshRoleBadge();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
