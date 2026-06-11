pipeline {
  agent any

  options {
    timestamps()
  }

  // Infrastructure endpoints are overridable via Jenkins global environment
  // variables so this file carries no hard requirement on internal addresses.
  // Set POKERING_GIT_URL / POKERING_ANSIBLE_TARGET / POKERING_ANSIBLE_DIR in
  // Jenkins (Manage Jenkins → System → Global properties) to drop the
  // fallbacks below before this repo travels to any wider audience.
  environment {
    GIT_REPO_URL = "${env.POKERING_GIT_URL ?: 'git@10.1.0.16:rtjipjes/pokering-points-app.git'}"
    ANSIBLE_TARGET = "${env.POKERING_ANSIBLE_TARGET ?: 'ansible@10.1.0.17'}"
    ANSIBLE_DIR = "${env.POKERING_ANSIBLE_DIR ?: '/opt/infra-ansible'}"
  }

  stages {
    stage('Checkout') {
      steps {
        git branch: 'main', url: env.GIT_REPO_URL
      }
    }

    stage('Python lint') {
      steps {
        sh label: 'Create Python virtualenv', script: '''#!/bin/sh
          set -eu
          python3 -m venv .venv
        '''
        sh label: 'Install Python dependencies', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          pip install --upgrade pip
          pip install -r requirements.txt -r requirements-dev.txt
        '''
        sh label: 'Black check', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          black --check .
        '''
        sh label: 'Ruff check', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          ruff check .
        '''
      }
    }

    stage('Tests') {
      steps {
        sh label: 'Pytest', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          pytest
        '''
      }
    }

    stage('Frontend lint') {
      steps {
        sh label: 'Install frontend dependencies', script: '''#!/bin/sh
          set -eu
          if [ -f package-lock.json ]; then
            npm ci --no-audit --no-fund
          else
            npm install --no-audit --no-fund
          fi
        '''
        sh label: 'npm lint', script: '''#!/bin/sh
          set -eu
          npm run lint
        '''
        sh label: 'npm format', script: '''#!/bin/sh
          set -eu
          npm run format
        '''
      }
    }

    stage('Dependency audit') {
      steps {
        sh label: 'Python dependency audit', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          pip-audit -r requirements.txt
        '''
        sh label: 'npm dependency audit', script: '''#!/bin/sh
          set -eu
          npm run audit:deps
        '''
      }
    }

    // Deploy only on release tags. Pushing main runs lint/tests/audit and
    // stops there; to ship, tag the commit and push the tag:
    //   git tag v2.2.0 && git push forgejo main --tags
    // The stage deploys when the built HEAD of main carries a v* tag, so a
    // tag on an older commit never ships by accident. The Forgejo→Jenkins
    // webhook must include tag-push events for the tag build to fire.
    stage('Deploy') {
      when {
        expression {
          sh label: 'Fetch tags', script: 'git fetch --tags --quiet'
          env.RELEASE_TAG = sh(
            script: "git tag --points-at HEAD | grep -E '^v[0-9]' | sort -V | tail -n1",
            returnStdout: true
          ).trim()
          if (env.RELEASE_TAG) {
            echo "Release tag ${env.RELEASE_TAG} points at HEAD — deploying"
          } else {
            echo 'No release tag on HEAD — skipping deploy'
          }
          return env.RELEASE_TAG
        }
      }
      steps {
        sh label: 'Deploy with Ansible', script: '''#!/bin/sh
          set -eu
          ssh -o BatchMode=yes "$ANSIBLE_TARGET" \
            "cd $ANSIBLE_DIR && ansible-playbook playbooks/pokering-deploy.yml"
        '''
      }
    }
  }
}
