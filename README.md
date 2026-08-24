# MounThor
CIFS/SMB mount manager for Linux.
[screenshot]
A simple GTK4/libadwaita GUI for mounting SMB shares on Linux using the kernel's CIFS/SMB filesystem client.

Save frequently used shares along with their mount settings and connect them with just a few clicks. For credentials, you can either save them in the application's configuration file or enter the password each time a share is mounted. You can also use your current Linux account name as the SMB username without storing it in the configuration.
The app provides batch actions such as Connect All, Disconnect All, Connect Selected, and Disconnect Selected, allowing you to authenticate with your superuser password once and apply the action to multiple shares. Individual shares can also be configured to automatically mount when the application starts.

The goal is to provide a simple and elegant GUI for managing CIFS/SMB mounts without maintaining persistent mounts through /etc/fstab or systemd units."
## Features
- ...
- ...
- ...
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
