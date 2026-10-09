# Building and releasing MounThor

## Version and release date

`APP_VERSION` and `APP_RELEASE_DATE` in the root `mounthor.py` are the single
sources for the application's release version and date. Write the date as a
full English date, for example `10 October 2026`. AppImage and source archive
filenames read `APP_VERSION` directly. Run the synchronizer after changing
either value so package metadata and AppStream release information match:

```sh
./tools/sync_package_versions.py
```

The synchronizer updates:

- the top Debian changelog entry and its date;
- the RPM spec version, release, and top changelog entry/date;
- the AppStream release version and ISO date in
  `data/io.github.mizgo.MounThor.metainfo.xml`.

When preparing a new application release, the synchronizer also resets Debian
and RPM package revisions to `1` (`-1` in Debian and `1%{?dist}` in RPM). For a
package-only fix with the same application version, increment that package's
revision manually and do not run the synchronizer afterward.

Typical release sequence:

1. Set `APP_VERSION` and `APP_RELEASE_DATE` in `mounthor.py`.
2. Run `./tools/sync_package_versions.py`.
3. Build the formats you plan to distribute using the commands below.
4. Check the generated files in `dist/`.

Each build script can be run from any directory; it finds the project root from
its own location. The output paths below are relative to the project root.

## Build formats

### Source archive

```sh
./packaging/build-source.sh
```

Creates `dist/MounThor-<version>-source.tar.gz`. It packages the application,
install and helper scripts, desktop assets, Debian metadata, tools, and
packaging files into a versioned source archive. This is a source distribution;
it does not compile the application or install it.

### Debian package (`.deb`)

Build on Debian or a Debian-based distribution with `dpkg-dev` and `debhelper`
installed:

```sh
./packaging/build-deb.sh
```

The script checks that the Debian changelog's upstream version matches
`APP_VERSION`, prepares a temporary build tree under `dist/`, and invokes
`dpkg-buildpackage`. The generated package is placed in `dist/`. Runtime
requirements are declared in `debian/control`; `python3-secretstorage` and
`gnome-keyring` are suggestions because Secret Service support is optional.

### RPM package (`.rpm`)

Build on Rocky Linux 9 or a compatible EL9 system with `rpm-build` installed:

```sh
./packaging/build-rpm.sh
```

The script checks that the RPM spec's `Version` matches `APP_VERSION`, creates
the versioned source archive required by RPM, and runs `rpmbuild`. The binary
RPM is written to `dist/rpm-build/rpmbuild/RPMS/noarch/`; the source RPM is
written to the corresponding `SRPMS/` directory. Build on EL9 to target Rocky
Linux 9's runtime libraries. The RPM uses the system's Python, GTK4, and
libadwaita packages. `python3-secretstorage` is recommended but not required.

### AppImage

Build on a Linux system with `appimagetool` and Python 3 installed:

```sh
./packaging/build-appimage.sh
```

The script assembles `dist/appimage/MounThor.AppDir/`, then uses
`appimagetool` to create `dist/appimage/MounThor-<version>-x86_64.AppImage`.
The AppImage contains MounThor, its mount helper, and desktop assets. It uses
the target system's Python, PyGObject, GTK4, and libadwaita at runtime; it does
not bundle those libraries. A PikaOS-built AppImage can therefore use Rocky
9.8's older system libraries when the required runtime dependencies are
installed and the AppImage runtime itself is compatible. Verify the result on
the target distribution before release. Output is currently x86_64.

## Per-user shell installer

The installer is for installing directly from a checked-out or unpacked source
tree, without a package manager or administrator privileges:

```sh
./scripts/install-mounthor.sh
```

It installs the app under the user's XDG data directory and adds a launcher to
`~/.local/bin`. It does not use the source archive produced by
`build-source.sh`; that archive simply includes the installer for distribution.

To remove the per-user installation:

```sh
./scripts/uninstall-mounthor.sh
```

The uninstaller asks before removing application files and separately asks
whether to remove configuration and logs. It may also offer to remove the
system polkit rule, which requires administrator privileges. Package-manager
installations should instead be removed with the matching package manager.