from flask import current_app
from ..extensions import db
from ..models import Role, Product

# Issue #18: único ponto de definição do catálogo estrutural mínimo do HUB
# (papéis e produtos). CLI, serviço e testes devem sempre importar estas
# constantes - nunca redeclarar os identificadores/metadados em outro lugar.
#
# Metadados extraídos das únicas fontes já existentes no projeto para cada
# produto (seed_data.py, histórico). A URL de cada produto NÃO é
# armazenada aqui como valor fixo - o catálogo guarda apenas a chave de
# configuração (`url_config_key`) correspondente já existente em
# app/config.py (Config.L_KALENDER_URL/L_GEDO_URL/L_HUNT_URL, todas
# sobrescrevíveis por variável de ambiente). O valor real da URL só é
# resolvido em tempo de execução, via `current_app.config`, dentro de
# `ensure_structural_catalog()` - nunca lido da classe Config no momento do
# import deste módulo, para não congelar um valor de um ambiente errado.

STRUCTURAL_ROLES = (
    {
        "name": "owner",
        "description": (
            "Proprietário da organização - papel administrativo máximo "
            "dentro da organização. Toda organização precisa manter ao "
            "menos um OWNER ativo (ver OrganizationService)."
        ),
    },
    {
        "name": "member",
        "description": "Membro padrão da organização, sem privilégios administrativos adicionais.",
    },
)

STRUCTURAL_PRODUCTS = (
    {
        "code": "kalender",
        "name": "L-Kalender",
        "description": "Gestão de prazos e eventos de licitação.",
        "url_config_key": "L_KALENDER_URL",
    },
    {
        "code": "gedo",
        "name": "L-GeDo",
        "description": "Gestão Eletrônica de Documentos para concorrências.",
        "url_config_key": "L_GEDO_URL",
    },
    {
        "code": "hunt",
        "name": "L-Hunt",
        "description": "Inteligência e monitoramento de editais de compras públicas.",
        "url_config_key": "L_HUNT_URL",
    },
)


class StructuralCatalogConflictError(Exception):
    """Levantada quando um Role/Product já existente diverge do catálogo
    canônico (mesmo identificador único, metadados diferentes).

    Carrega apenas identificadores (nomes de papel / códigos de produto) -
    nunca URLs completas, descrições divergentes ou qualquer outro dado do
    registro em conflito - para que a mensagem de erro nunca vaze conteúdo
    potencialmente sensível ou específico de ambiente.
    """

    def __init__(self, conflicting_roles, conflicting_products):
        self.conflicting_roles = list(conflicting_roles)
        self.conflicting_products = list(conflicting_products)
        identifiers = self.conflicting_roles + self.conflicting_products
        super().__init__(
            "Catálogo estrutural em conflito - identificadores já existentes com "
            f"metadados diferentes do catálogo oficial: {', '.join(identifiers)}. "
            "Nenhuma alteração foi salva; resolva a divergência manualmente antes "
            "de tentar novamente."
        )


class BootstrapService:
    """Bootstrap idempotente do catálogo estrutural mínimo (Role/Product).

    Não cria User, Organization, OrganizationMember, Permission,
    ProductPermission, nem concede/altera acesso de produto a nenhuma
    organização - apenas garante a existência dos dois catálogos
    estruturais definidos em STRUCTURAL_ROLES/STRUCTURAL_PRODUCTS acima.
    """

    @staticmethod
    def ensure_structural_catalog():
        """Garante a existência dos papéis e produtos estruturais mínimos.

        Executa em duas fases estritamente sequenciais:

        1. Validação (somente leitura, nenhuma gravação): cada Role/Product
           do catálogo é comparado, por identificador único (Role.name /
           Product.code), contra um registro já existente no banco. Um
           registro ausente é anotado para criação; um registro existente
           com metadados canônicos (description; para Product também name
           e a URL resolvida de `current_app.config`) é anotado como já
           compatível; um registro existente com qualquer metadado
           divergente é anotado como conflito.

           Se QUALQUER conflito for encontrado (em Role OU em Product),
           TODOS são coletados antes de decidir - e a operação inteira é
           abortada: nenhum registro ausente é criado (nem os que não têm
           conflito), nenhum `db.session.add`/`commit` é executado, e
           `StructuralCatalogConflictError` é levantada com a lista de
           identificadores em conflito (nunca URLs/descrições completas).
           Não há sucesso parcial.

        2. Criação (somente se a fase 1 não encontrou nenhum conflito):
           os registros ausentes são adicionados à sessão e persistidos por
           um único `db.session.commit()`. Qualquer falha durante essa
           persistência reverte tudo por completo (`db.session.rollback()`)
           antes de propagar o erro.
        """
        missing_roles = []
        existing_roles = []
        conflicting_roles = []

        for role_spec in STRUCTURAL_ROLES:
            existing = Role.query.filter_by(name=role_spec["name"]).first()
            if existing is None:
                missing_roles.append(role_spec)
            elif existing.description == role_spec["description"]:
                existing_roles.append(role_spec["name"])
            else:
                conflicting_roles.append(role_spec["name"])

        missing_products = []
        existing_products = []
        conflicting_products = []

        for product_spec in STRUCTURAL_PRODUCTS:
            canonical_url = current_app.config.get(product_spec["url_config_key"])
            existing = Product.query.filter_by(code=product_spec["code"]).first()
            if existing is None:
                missing_products.append((product_spec, canonical_url))
            elif (
                existing.name == product_spec["name"]
                and existing.description == product_spec["description"]
                and existing.url == canonical_url
            ):
                existing_products.append(product_spec["code"])
            else:
                conflicting_products.append(product_spec["code"])

        if conflicting_roles or conflicting_products:
            # Nenhuma gravação foi feita até aqui (fase somente leitura) -
            # o rollback aqui é uma garantia defensiva explícita de que
            # nenhum estado pendente (ex.: autoflush) sobrevive, mesmo que
            # nada tenha sido de fato adicionado à sessão.
            db.session.rollback()
            raise StructuralCatalogConflictError(conflicting_roles, conflicting_products)

        for role_spec in missing_roles:
            db.session.add(Role(name=role_spec["name"], description=role_spec["description"]))

        for product_spec, canonical_url in missing_products:
            db.session.add(Product(
                code=product_spec["code"],
                name=product_spec["name"],
                description=product_spec["description"],
                url=canonical_url,
            ))

        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise ValueError(
                "Não foi possível concluir o bootstrap estrutural. Nenhuma alteração foi salva."
            )

        return {
            "created_roles": [role_spec["name"] for role_spec in missing_roles],
            "existing_roles": existing_roles,
            "conflicting_roles": [],
            "created_products": [product_spec["code"] for product_spec, _ in missing_products],
            "existing_products": existing_products,
            "conflicting_products": [],
        }
