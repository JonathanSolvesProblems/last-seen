import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// The DJI app key lives in local.properties (not committed):
//   DJI_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
// Create it at https://developer.dji.com (Mobile SDK, package name below).
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val djiApiKey: String = localProps.getProperty("DJI_API_KEY") ?: System.getenv("DJI_API_KEY") ?: ""

android {
    namespace = "com.jonathanandrei.phantombridge"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.jonathanandrei.phantombridge"
        minSdk = 26
        // MSDK v4 predates Android 14's foreground-service rules; stay on 33.
        targetSdk = 33
        versionCode = 1
        versionName = "0.1"
        multiDexEnabled = true
        manifestPlaceholders["DJI_API_KEY"] = djiApiKey
        ndk {
            abiFilters += listOf("armeabi-v7a", "arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        jniLibs {
            // DJI ships prelinked libs that must not be stripped.
            keepDebugSymbols += listOf(
                "*/*/libdjivideo.so", "*/*/libSDKRelativeJNI.so", "*/*/libFlyForbid.so",
                "*/*/libduml_vision_bokeh.so", "*/*/libyuv2.so", "*/*/libGroudStation.so",
                "*/*/libFRCorkscrew.so", "*/*/libUpgradeVerify.so", "*/*/libFR.so",
                "*/*/libDJIFlySafeCore.so", "*/*/libdjifs_jni.so", "*/*/libsfjni.so",
            )
            useLegacyPackaging = true
        }
        resources {
            excludes += listOf("META-INF/rxjava.properties", "META-INF/DEPENDENCIES",
                "META-INF/LICENSE*", "META-INF/NOTICE*", "META-INF/*.kotlin_module")
        }
    }

    lint {
        abortOnError = false
    }
}

dependencies {
    implementation("com.dji:dji-sdk:4.18") {
        // Optional pieces we do not need; keeps the APK ~100 MB smaller.
        exclude(module = "library-anti-distortion")
        exclude(module = "fly-safe-database")
    }
    compileOnly("com.dji:dji-sdk-provided:4.18")

    implementation("org.java-websocket:Java-WebSocket:1.5.7")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.multidex:multidex:2.0.1")
    implementation("com.google.android.material:material:1.12.0")
}
