[app]
title = 舌征智析
package.name = shezhengzhixi
package.domain = com.shezhengzhixi.app
source.dir = .
source.include_exts = py,kv,json,png,jpg,jpeg,webp,db,ttf,otf,ttc
version = 0.1.5
orientation = portrait
fullscreen = 1

requirements = python3,kivy==2.3.1,kivymd==1.2.0,requests==2.32.3,python-dateutil==2.9.0.post0,plyer==2.1.0,filetype==1.2.0

android.permissions = INTERNET,CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.accept_sdk_license = True

# 避免把无关大文件打包进 APK
source.exclude_patterns = .git/*,.venv/*,venv/*,__pycache__/*,*.pyc,*.log,bin/*,.buildozer/*

[buildozer]
log_level = 2
warn_on_root = 1
