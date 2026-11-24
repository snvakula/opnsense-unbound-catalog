PLUGIN_NAME=		unbound-catalog
PLUGIN_VERSION=		0.3.0
PLUGIN_COMMENT=		Manage Unbound DNS Catalog Zone synchronization
PLUGIN_DEPENDS=		python313 py313-dnspython
PLUGIN_MAINTAINER=	snvakula@gmail.com
PLUGIN_WWW=		https://github.com/snvakula/opnsense-unbound-catalog
PLUGIN_LICENSE=		BSD2CLAUSE

PLUGINSDIR?=		${.CURDIR}/../..
.include "${PLUGINSDIR}/Mk/plugins.mk"
