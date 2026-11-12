plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "cn.ykt.helper"
    compileSdk = 34

    defaultConfig {
        applicationId = "cn.ykt.helper"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    // 签名信息从环境变量读，仓库里不放 keystore（release.keystore 已在 .gitignore）
    signingConfigs {
        create("release") {
            val ks = System.getenv("YKT_KEYSTORE") ?: "${rootDir}/release.keystore"
            val ksFile = file(ks)
            if (ksFile.exists()) {
                storeFile = ksFile
                storePassword = System.getenv("YKT_STORE_PASSWORD") ?: "yuketang"
                keyAlias = System.getenv("YKT_KEY_ALIAS") ?: "ykt"
                keyPassword = System.getenv("YKT_KEY_PASSWORD") ?: "yuketang"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfigs.findByName("release")?.storeFile?.let { signingConfig = signingConfigs.getByName("release") }
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.zxing:core:3.5.2")
}
