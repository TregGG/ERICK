# Android apps

Two thin Android apps around the live website. Both open `https://campusrick.centralindia.cloudapp.azure.com`,
so **website changes reach the apps without a new app build**. Rebuild only when something in `android/` changes.

| App | Folder | Package | How it works |
| --- | --- | --- | --- |
| Campus Rick (passenger) | `passenger/` | `in.ac.iitr.campusrick.passenger` | Trusted Web Activity: opens `/passenger/` full-screen in Chrome (`androidbrowserhelper`) |
| Rick Driver | `driver/` | `in.ac.iitr.campusrick.driver` | Capacitor app: loads `/driver/` in a WebView and adds native background location |

The driver app exists because browsers stop GPS when the screen is off. Inside the app,
`shared/js/geo.js` detects Capacitor and takes positions from the
`@capacitor-community/background-geolocation` foreground service instead, so location keeps
flowing while on duty, with an Android "on duty" notification. In a normal browser nothing changes.

## Building

GitHub Actions (`.github/workflows/android.yml`) builds both apps on every push to `android` or `master`
that touches `android/`, and can be started by hand (Actions → Android apps → Run workflow).
Download the APKs from the run's **Artifacts** section and install them on a phone
(allow "install unknown apps" for your browser or file manager).

Without signing secrets the workflow makes **debug** APKs. With them it makes signed **release** APKs
and `.aab` bundles for the Play Store, and publishes them on the repository's **Releases** page as a
pre-release named "Android beta N" (tag `android-beta-N`, N = workflow run number). Share that page
with testers; they install the APK straight from it.

### Signing (one-time)

The release key lives outside the repo, in `~/.android-keys/` on the machine that created it:

- `campusrick-release.jks`: the key. **Back it up** (password manager or encrypted drive). Without it the apps can never be updated on the Play Store.
- `campusrick-release.txt`: alias and password.
- `campusrick-release.jks.base64`: the same key as text, for the GitHub secret.

Add four repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | contents of `campusrick-release.jks.base64` (`wl-copy --type text/plain < ~/.android-keys/campusrick-release.jks.base64`) |
| `ANDROID_KEYSTORE_PASSWORD` | the password from `campusrick-release.txt` |
| `ANDROID_KEY_PASSWORD` | the same password |
| `ANDROID_KEY_ALIAS` | `campusrick` |

### Full-screen passenger app (Digital Asset Links)

Chrome hides the URL bar only if the site vouches for the app. The backend serves
`/.well-known/assetlinks.json` from two settings in the server's `backend/.env`:

```ini
ANDROID_TWA_PACKAGE=in.ac.iitr.campusrick.passenger
ANDROID_TWA_SHA256=<SHA-256 of the signing certificate>
```

The release build prints the fingerprint as a notice on the Actions run ("Signing certificate SHA-256").
If the app is later published with **Play App Signing**, add Google's fingerprint too (Play Console →
App integrity), comma-separated. Restart the backend after changing `.env`.

## Changing things

- **Icons:** edit `shared/icons/<app>.svg`, then run `android/make-icons.sh passenger android/passenger/app/src/main/res`
  and `android/make-icons.sh driver android/driver/android/app/src/main/res` (needs `rsvg-convert` and ImageMagick).
- **Server address:** `twaHost` in `passenger/gradle.properties` and `server.url` in `driver/capacitor.config.json`.
- **Driver app permissions / plugin:** `driver/package.json`, then `npm install && npx cap sync android` in `driver/`.
