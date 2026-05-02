import io.gitlab.arturbosch.detekt.Detekt
import org.gradle.api.GradleException
import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import java.io.File

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.detekt)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

private val canonicalReleaseVersionPattern = Regex("""\d+\.\d+\.\d+""")
private val releaseSigningExemptTaskMarkers =
    listOf(
        "lint",
        "test",
        "ktlint",
        "detekt",
        "uninstall",
    )

private val releaseSigningRequiredTaskMarkers =
    listOf(
        "assemble",
        "bundle",
        "package",
        "publish",
        "sign",
        "extractapks",
        "zipapks",
    )

private data class ReleaseSigningInputs(
    val storeFile: File,
    val storePassword: String,
    val keyAlias: String,
    val keyPassword: String,
)

private fun readCanonicalReleaseVersion(): String {
    val repoRoot =
        rootProject.projectDir.parentFile
            ?: throw GradleException(
                "android root ${rootProject.projectDir} has no parent directory for repo root lookup",
            )
    val versionFile = repoRoot.resolve("VERSION")
    if (!versionFile.isFile) {
        throw GradleException("canonical VERSION file not found at ${versionFile.absolutePath}")
    }
    val rawVersion = versionFile.readText(Charsets.UTF_8)
    val canonicalVersion =
        when {
            rawVersion.endsWith("\r\n") -> rawVersion.removeSuffix("\r\n")
            rawVersion.endsWith("\n") -> rawVersion.removeSuffix("\n")
            else -> rawVersion
        }
    if (!canonicalReleaseVersionPattern.matches(canonicalVersion)) {
        throw GradleException(
            "canonical VERSION at ${versionFile.absolutePath} must contain exactly MAJOR.MINOR.PATCH with at most one terminating newline",
        )
    }
    return canonicalVersion
}

private fun deriveAndroidVersionCode(canonicalVersion: String): Int {
    val segments = canonicalVersion.split(".")
    if (segments.size != 3) {
        throw GradleException(
            "Android versionCode requires canonical VERSION to be MAJOR.MINOR.PATCH, got $canonicalVersion",
        )
    }

    val major =
        segments[0].toLongOrNull()
            ?: throw GradleException(
                "Android versionCode requires numeric major version, got ${segments[0]}",
            )
    val minor =
        segments[1].toLongOrNull()
            ?: throw GradleException(
                "Android versionCode requires numeric minor version, got ${segments[1]}",
            )
    val patch =
        segments[2].toLongOrNull()
            ?: throw GradleException(
                "Android versionCode requires numeric patch version, got ${segments[2]}",
            )

    if (minor !in 0L..999L) {
        throw GradleException(
            "Android versionCode requires minor in 0..999, got $minor for $canonicalVersion",
        )
    }
    if (patch !in 0L..999L) {
        throw GradleException(
            "Android versionCode requires patch in 0..999, got $patch for $canonicalVersion",
        )
    }

    val versionCode = (major * 1_000_000L) + (minor * 1_000L) + patch
    if (versionCode < 1L || versionCode > 2_100_000_000L) {
        throw GradleException(
            "Android versionCode must be in 1..2_100_000_000, got $versionCode for $canonicalVersion",
        )
    }
    return versionCode.toInt()
}

private fun readReleaseSigningInputsIfRequested(
    taskNames: List<String>,
    fileResolver: (String) -> File,
): ReleaseSigningInputs? {
    if (!taskNames.any(::isReleasePackagingTask)) {
        return null
    }

    val storeFilePath = readRequiredEnv("ANDROIDCTL_RELEASE_STORE_FILE")
    val storeFile = fileResolver(storeFilePath)
    if (!storeFile.isFile) {
        throw GradleException(
            "ANDROIDCTL_RELEASE_STORE_FILE must point to an existing keystore file: ${storeFile.absolutePath}",
        )
    }

    return ReleaseSigningInputs(
        storeFile = storeFile,
        storePassword = readRequiredEnv("ANDROIDCTL_RELEASE_STORE_PASSWORD"),
        keyAlias = readRequiredEnv("ANDROIDCTL_RELEASE_KEY_ALIAS"),
        keyPassword = readRequiredEnv("ANDROIDCTL_RELEASE_KEY_PASSWORD"),
    )
}

private fun isReleasePackagingTask(taskName: String): Boolean = requiresReleaseSigning(taskName.substringAfterLast(':').lowercase())

private fun requiresReleaseSigning(taskName: String): Boolean {
    if ("release" !in taskName) {
        return false
    }
    if (releaseSigningExemptTaskMarkers.any(taskName::contains)) {
        return false
    }
    if (taskName.startsWith("buildrelease")) {
        return true
    }
    if (taskName.startsWith("installrelease")) {
        return true
    }
    return releaseSigningRequiredTaskMarkers.any(taskName::contains)
}

private fun readRequiredEnv(name: String): String {
    val value = System.getenv(name)
    if (value.isNullOrBlank()) {
        throw GradleException(
            "Missing required environment variable $name for Android release signing.",
        )
    }
    return value
}

private val canonicalReleaseVersion = readCanonicalReleaseVersion()
private val canonicalReleaseVersionCode = deriveAndroidVersionCode(canonicalReleaseVersion)
private val releaseSigningInputs =
    readReleaseSigningInputsIfRequested(gradle.startParameter.taskNames, ::file)

android {
    namespace = "com.rainng.androidctl"
    compileSdk {
        version = release(36)
    }

    signingConfigs {
        create("release") {
            if (releaseSigningInputs != null) {
                storeFile = releaseSigningInputs.storeFile
                storePassword = releaseSigningInputs.storePassword
                keyAlias = releaseSigningInputs.keyAlias
                keyPassword = releaseSigningInputs.keyPassword
            }
        }
    }

    defaultConfig {
        applicationId = "com.rainng.androidctl"
        minSdk = 30
        targetSdk = 36
        versionCode = canonicalReleaseVersionCode
        versionName = canonicalReleaseVersion
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        buildConfig = true
        compose = true
    }
    lint {
        abortOnError = true
        checkAllWarnings = true
        warningsAsErrors = true
        checkDependencies = true
        informational +=
            setOf(
                "AndroidGradlePluginVersion",
                "GradleDependency",
                "NewerVersionAvailable",
            )
    }
}

androidComponents {
    beforeVariants(selector().all()) { variantBuilder ->
        variantBuilder.enableAndroidTest = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_11)
    }
}

detekt {
    buildUponDefaultConfig = true
    allRules = false
    parallel = true
    autoCorrect = false
    config.setFrom("$rootDir/detekt.yml")
    basePath = rootDir.absolutePath
}

tasks.withType<Detekt>().configureEach {
    jvmTarget = "11"
    reports {
        html.required.set(true)
        sarif.required.set(true)
        txt.required.set(false)
        xml.required.set(true)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.material3)
    implementation(libs.nanohttpd)
    testImplementation(libs.json)
    testImplementation(libs.junit)
    testImplementation(libs.mockito.core)
}
