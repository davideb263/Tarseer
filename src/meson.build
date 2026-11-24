blueprints = custom_target('blueprints',
  input: files(
    'tarseer/ui/window.blp',
    'tarseer/ui/app_row.blp',
    'tarseer/ui/add_dialog.blp',
    'tarseer/ui/details_dialog.blp',
    'tarseer/ui/release_file_dialog.blp',
  ),
  output: '.',
  command: [find_program('blueprint-compiler'), 'batch-compile', '@OUTPUT@', '@CURRENT_SOURCE_DIR@', '@INPUT@'],
)

gnome.compile_resources('tarseer',
  'tarseer.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
  dependencies: blueprints,
)

install_subdir('tarseer',
  install_dir: pkgdatadir,
  exclude_directories: ['__pycache__', 'ui/__pycache__', 'core/__pycache__'],
)
