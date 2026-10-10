Name:           mounthor
Version:        0.10.2
Release:        1%{?dist}
Summary:        GTK4/libadwaita SMB/CIFS share manager
License:        GPL-3.0-only
URL:            https://github.com/mizgo/MounThor
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.9
Requires:       python3-gobject
Requires:       gtk4
Requires:       libadwaita
Requires:       cifs-utils
Requires:       polkit
Recommends:     python3-secretstorage

%description
MounThor stores SMB share settings and mounts CIFS network shares using a
GTK4 and libadwaita desktop interface. Secret Service support is optional.

%post
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f %{_datadir}/icons/hicolor || :
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q %{_datadir}/applications || :
fi

%postun
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t -f %{_datadir}/icons/hicolor || :
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q %{_datadir}/applications || :
fi

%prep
%autosetup -n %{name}-%{version}

%build

%install
install -Dpm 0644 mounthor.py %{buildroot}%{_prefix}/lib/mounthor/mounthor.py
install -Dpm 0755 scripts/mounthor-mount-helper %{buildroot}%{_prefix}/lib/mounthor/scripts/mounthor-mount-helper
install -Dpm 0755 packaging/linux/mounthor %{buildroot}%{_bindir}/mounthor
install -Dpm 0644 data/io.github.mizgo.MounThor.desktop %{buildroot}%{_datadir}/applications/io.github.mizgo.MounThor.desktop
install -Dpm 0644 data/io.github.mizgo.MounThor.metainfo.xml %{buildroot}%{_datadir}/metainfo/io.github.mizgo.MounThor.metainfo.xml
install -Dpm 0644 data/icons/hicolor/scalable/apps/io.github.mizgo.MounThor.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.github.mizgo.MounThor.svg

%files
%license LICENSE
%{_bindir}/mounthor
%{_prefix}/lib/mounthor/
%{_datadir}/applications/io.github.mizgo.MounThor.desktop
%{_datadir}/metainfo/io.github.mizgo.MounThor.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.mizgo.MounThor.svg

%changelog
* Sat Oct 10 2026 MounThor contributors <mizgo@users.noreply.github.com> - 0.10.2-1
- Prepare native RPM packaging.
