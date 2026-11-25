HLD Vita Builder
================

Makes the unofficial PS Vita port of Hyper Light Drifter from your own copy of the game.
You need Hyper Light Drifter on Steam. This kit contains no game files.

1. Start it
   Windows:  double-click "HLD Vita Builder.bat"
   macOS:    double-click "HLD Vita Builder.command"
             (first time: right-click > Open, because it is not from the App Store)

2. Sign in to Steam if asked
   If the August 2016 build of the game is not already on this computer, the builder downloads it from Steam
   (about 1 GB). Type your Steam username; the Steam download tool then asks for your password and Steam Guard code
   itself. The builder never sees or stores them. Steam only allows the download if your account owns the game.
   To sign in with the Steam mobile app instead, start the builder with --qr.

3. Wait
   The first run takes 20-40 minutes. It downloads its tools (~250 MB), decompiles and translates the game scripts,
   and repacks the textures. Later runs reuse all of that.

4. Copy it to your Vita
   The VPK is small (~7 MB): it is only the app. The game itself (~1.9 GB: data.win, textures, music, rooms) goes to
   ux0:data/hld-vm on the Vita. At the end the builder offers to copy everything, either way:
     Wi-Fi: on the Vita open VitaShell and press SELECT to start FTP; type the IP address it shows.
     USB:   on the Vita open VitaShell, press START, set "USB device" to Memory Card, go back, press SELECT and
            connect the cable; type usb. (Much faster than Wi-Fi.) When it is done, eject the card on the computer
            and press O on the Vita.
   To do it later, double-click "Copy to Vita.bat" (Windows) or "Copy to Vita.command" (macOS) in this folder.
   Then, on the Vita, open VitaShell, go to ux0:data, press X on HyperLightDrifter.vpk to install it, and launch
   "Hyper Light Drifter". Your saves are never touched.

The app you build contains code made from your copy of the game: do not share it, or the work/ folder.

Source code, issues and full documentation: see the project's GitHub page. Licence: AGPL-3.0 (LICENSE);
third-party notices in NOTICE.md.
