# MounThor
CIFS/SMB mount manager for Linux.

<table>
  <tr>
    <td align="center">
      <strong>Dark theme</strong><br>
      <img src="data/screenshots/main-window_dark.png" alt="MounThor in dark theme" width="400">
    </td>
    <td align="center">
      <strong>Light theme</strong><br>
      <img src="data/screenshots/main-window_light.png" alt="MounThor in light theme" width="400">
    </td>
  </tr>
</table>

A simple GTK4/libadwaita GUI for mounting SMB shares on Linux using the kernel's CIFS/SMB filesystem client.

Save frequently used shares along with their mount settings and connect them with just a few clicks. For credentials, you can either save them in the application's configuration file or enter the password each time a share is mounted. You can also use your current Linux account name as the SMB username without storing it in the configuration.
The app provides batch actions for connecting and disconnecting all shares or selected shares, allowing you to authenticate with your superuser password once and apply the action to multiple shares. Individual shares can also be configured to automatically mount when the application starts.
The interface follows the system's GTK light and dark themes and respects the configured accent color.

The goal is to provide a simple and elegant GUI for managing CIFS/SMB mounts without maintaining persistent mounts through /etc/fstab or systemd units."
## Features
- Save frequently used SMB shares and their mount settings.
- Mount and unmount shares individually with a toggle.
- Select multiple shares and connect or disconnect them as a batch.
- Connect All and Disconnect All actions.
- Clear Selection for quickly deselecting multiple shares.
- Batch password handling with a single superuser authentication.
- Optionally remember SMB passwords in the application configuration.
- Use the current Linux account name as the SMB username without storing it.
- Configure custom CIFS mount options such as vers=3.1.1.
- Configure shares to automount when MounThor starts.
- Edit, duplicate, and remove saved shares.
- Automatically clean up temporary CIFS credential files left after an unexpected application exit.
- GTK4/libadwaita interface supporting system light and dark themes and the configured accent color.
- Responsive status feedback with spinners, icons, toasts, and logging.
## Requirements
- Linux
- Python ...
- GTK4
- libadwaita
- cifs-utils
- polkit / pkexec
## Installation
...
## License
MounThor is licensed under the GNU GPL v3.0.
