project('tarseer',
  version: '0.3.0',
  meson_version: '>= 1.0.0',
  license: 'GPL-3.0-or-later',
)

app_id = 'io.github.davideb263.Tarseer'

gnome = import('gnome')
python = import('python')

pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()

subdir('data')
subdir('src')
subdir('bin')

gnome.post_install(
  glib_compile_schemas: true,
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
