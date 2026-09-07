-- Enforce: a tenant must always have at least one owner (Fix R4.1)
-- This trigger fires AFTER any UPDATE or DELETE on tenant_memberships
-- and raises an exception if the statement would leave any tenant with zero owners.
-- Using a DEFERRABLE INITIALLY DEFERRED constraint trigger ensures the check
-- runs at transaction commit, allowing multi-statement transactions that
-- temporarily violate the invariant (e.g., demote old owner + promote new owner).

CREATE OR REPLACE FUNCTION enforce_owner_count_invariant()
RETURNS TRIGGER AS $$
BEGIN
    -- Check all tenants affected by this statement for zero owners
    IF EXISTS (
        SELECT 1
        FROM tenant_memberships tm
        WHERE tm.tenant_id IN (
            SELECT DISTINCT tenant_id
            FROM tenant_memberships
            WHERE tenant_id = COALESCE(NEW.tenant_id, OLD.tenant_id)
               OR tenant_id IN (
                   SELECT DISTINCT tenant_id
                   FROM (VALUES (NEW.tenant_id), (OLD.tenant_id)) AS v(tenant_id)
                   WHERE tenant_id IS NOT NULL
               )
        )
        GROUP BY tm.tenant_id
        HAVING COUNT(*) FILTER (WHERE tm.role = 'owner') = 0
    ) THEN
        RAISE EXCEPTION 'Cannot remove or demote the last owner of a tenant';
    END IF;
    RETURN NULL; -- AFTER statement trigger returns NULL
END;
$$ LANGUAGE plpgsql;

-- Constraint trigger: deferrable, fires at transaction end
CREATE CONSTRAINT TRIGGER trg_enforce_owner_count
AFTER UPDATE OR DELETE ON tenant_memberships
DEFERRABLE INITIALLY DEFERRED
FOR EACH STATEMENT
EXECUTE FUNCTION enforce_owner_count_invariant();

-- Also protect against INSERT that could create a tenant with no owners
-- (though the application should always insert an owner first)
CREATE OR REPLACE FUNCTION enforce_owner_on_insert()
RETURNS TRIGGER AS $$
BEGIN
    -- After INSERT, check if the new tenant has at least one owner
    IF NOT EXISTS (
        SELECT 1
        FROM tenant_memberships
        WHERE tenant_id = NEW.tenant_id
          AND role = 'owner'
    ) THEN
        RAISE EXCEPTION 'Tenant must have at least one owner after membership creation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_enforce_owner_on_insert
AFTER INSERT ON tenant_memberships
DEFERRABLE INITIALLY DEFERRED
FOR EACH STATEMENT
EXECUTE FUNCTION enforce_owner_on_insert();