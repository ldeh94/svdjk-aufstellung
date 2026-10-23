plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Versionsnummer = Build-Nummer aus GitHub Actions
val buildNo = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
val ks = System.getenv("KEYSTORE_FILE")

android {
    namespace = "de.svdjk.aufstellung"
    compileSdk = 34

    defaultConfig {
        applicationId = "de.svdjk.aufstellung"
        minSdk = 26
        targetSdk = 34
        versionCode = buildNo
        versionName = "1.$buildNo"
    }

    signingConfigs {
        create("release") {
            if (ks != null && file(ks).exists()) {
                storeFile = file(ks)
                storeType = "pkcs12"
                storePassword = System.getenv("KEYSTORE_PASSWORD")
                keyAlias = System.getenv("KEY_ALIAS") ?: "svdjk"
                keyPassword = System.getenv("KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (ks != null) signingConfigs.getByName("release") else signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core:1.13.1")
}
